"""アップロードした Excel に結果の列を足す出力（#4212）のテスト。内容はすべて合成の値。"""

from __future__ import annotations

import zipfile
from collections.abc import AsyncIterator, Iterator
from io import BytesIO
from typing import Any

import httpx
import pytest
from lxml import etree  # type: ignore[import-untyped]
from openpyxl import Workbook, load_workbook  # type: ignore[import-untyped]
from openpyxl.comments import Comment  # type: ignore[import-untyped]
from openpyxl.styles import Font, PatternFill  # type: ignore[import-untyped]
from openpyxl.worksheet.datavalidation import DataValidation  # type: ignore[import-untyped]

from app.clients.oracle import OracleClient
from app.main import app
from app.rag import evaluation_source_workbooks
from app.rag.evaluation import EvaluationRunner
from app.rag.evaluation_jobs import (
    EvaluationJobService,
    MemoryEvaluationJobStore,
    set_evaluation_job_service,
)
from app.rag.evaluation_results_excel import evaluation_workbook_with_results
from app.rag.evaluation_source_workbooks import (
    EvaluationSourceWorkbookService,
    MemoryEvaluationSourceWorkbookStore,
    set_evaluation_source_workbook_service,
)
from app.rag.request_context import (
    AuditRequestContext,
    reset_audit_request_context,
    set_audit_request_context,
)
from app.rag.xlsx_append_columns import AppendedColumn, XlsxAppendError, append_columns
from app.schemas.evaluation import EvaluationCaseResult, EvaluationComparison, EvaluationMetrics
from tests.support import TEST_REQUEST_HEADERS

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _workbook() -> bytes:
    """質問が重なる行・書式・結合・入力規則・コメント・式・別のシートを持つ評価リクエスト。"""
    book = Workbook()
    sheet = book.active
    sheet.title = "評価リクエスト"
    sheet.append(["質問", "期待する根拠", "社内メモ"])
    sheet.append(["合成の質問", None, "=1+1"])
    sheet.append(["合成の質問", None, "メモ B"])
    sheet.append([None, None, None])
    sheet.append(["別の合成の質問", None, "メモ C"])
    sheet["A1"].font = Font(bold=True, color="FF0000")
    sheet["A2"].fill = PatternFill("solid", fgColor="FFFF00")
    sheet["C3"].comment = Comment("合成のコメント", "作成者")
    sheet.merge_cells("A7:C7")
    sheet["C8"] = "表の外の注記"
    sheet.column_dimensions["A"].width = 41
    sheet.row_dimensions[2].height = 33
    sheet.freeze_panes = "A2"
    validation = DataValidation(type="list", formula1='"はい,いいえ"')
    validation.add("C5")
    sheet.add_data_validation(validation)
    other = book.create_sheet("別のシート")
    other.append(["A", 1])
    book.create_sheet("空のシート")
    book.active = 1
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def _case(case_id: str, *, score: float | None = 0.5, **extra: Any) -> EvaluationCaseResult:
    return EvaluationCaseResult(
        case_id=case_id,
        trace_id=f"trace-{case_id}",
        elapsed_ms=1.0,
        answer_correctness=score,
        observed_outcome="answered",
        **extra,
    )


def _metrics(*cases: EvaluationCaseResult) -> EvaluationMetrics:
    return EvaluationMetrics(case_count=len(cases), case_results=list(cases))


def _headers(sheet: Any) -> dict[str, int]:
    return {
        str(cell.value): cell.column
        for cell in sheet[1]
        if isinstance(cell.value, str) and cell.value
    }


def _entries(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


# ---- xlsx の列の追加 ----


def test_only_the_target_sheet_and_styles_change_in_the_package() -> None:
    """ほかの部品（シート・共有文字列・テーマ・コメントなど）はバイト単位で変えない。"""
    data = _workbook()
    result = append_columns(
        data,
        sheet_title="評価リクエスト",
        columns=[AppendedColumn("結果")],
        rows={2: ["A"]},
    )
    before, after = _entries(data), _entries(result)
    assert before.keys() == after.keys()
    changed = {name for name in before if before[name] != after[name]}
    assert changed == {"xl/worksheets/sheet1.xml", "xl/styles.xml"}


def test_original_cells_format_and_structure_are_kept() -> None:
    result = append_columns(
        _workbook(),
        sheet_title="評価リクエスト",
        columns=[AppendedColumn("結果"), AppendedColumn("点", kind="percent", width=12)],
        rows={2: ["A", 0.5], 3: ["B", None], 5: ["C", 1.0]},
    )
    before = load_workbook(BytesIO(_workbook()))
    book = load_workbook(BytesIO(result))
    assert book.sheetnames == ["評価リクエスト", "別のシート", "空のシート"]
    assert book.active.title == "別のシート"
    old, new = before["評価リクエスト"], book["評価リクエスト"]
    for row in old.iter_rows():
        for cell in row:
            copied = new[cell.coordinate]
            assert copied.value == cell.value
            assert copied.font.b == cell.font.b
            assert copied.font.color == cell.font.color
            assert copied.fill.fgColor.rgb == cell.fill.fgColor.rgb
    assert new["C2"].value == "=1+1"
    assert new["C3"].comment.text == "合成のコメント"
    assert [str(item) for item in new.merged_cells.ranges] == ["A7:C7"]
    assert new.freeze_panes == "A2"
    assert new.column_dimensions["A"].width == 41
    assert new.row_dimensions[2].height == 33
    assert str(new.data_validations.dataValidation[0].sqref) == "C5"
    assert book["別のシート"]["B1"].value == 1


def test_values_go_to_the_requested_rows_right_of_the_used_range() -> None:
    result = append_columns(
        _workbook(),
        sheet_title="評価リクエスト",
        columns=[AppendedColumn("結果"), AppendedColumn("点", kind="percent", width=12)],
        rows={2: ["A", 0.5], 3: ["B", None], 5: ["C", 1.0]},
    )
    sheet = load_workbook(BytesIO(result))["評価リクエスト"]
    assert sheet["D1"].value == "結果"
    assert sheet["D1"].font.b is True
    assert sheet["D2"].value == "A"
    assert sheet["E2"].value == 0.5
    assert sheet["E2"].number_format == "0.0%"
    assert sheet["D3"].value == "B"
    assert sheet["E3"].value is None
    assert sheet["D4"].value is None
    assert sheet["D5"].value == "C"
    assert sheet.column_dimensions["E"].width == 12
    assert sheet.dimensions == "A1:E8"


def test_text_is_stored_as_text_not_formula_and_illegal_characters_are_removed() -> None:
    result = append_columns(
        _workbook(),
        sheet_title="評価リクエスト",
        columns=[AppendedColumn("結果")],
        rows={2: ["=SUM(A1:A2)\x01"], 3: ["  前後の空白  "], 5: ["長" * 40000]},
    )
    sheet = load_workbook(BytesIO(result))["評価リクエスト"]
    assert sheet["D2"].value == "=SUM(A1:A2)"
    assert sheet["D2"].data_type == "s"
    assert sheet["D3"].value == "  前後の空白  "
    assert len(sheet["D5"].value) <= 32767
    assert sheet["D5"].value.endswith("（省略）")


def test_appended_columns_keep_existing_column_ranges_from_overlapping() -> None:
    """全列にかかる列の定義があっても、列の定義が重ならない（重なると Excel が修復を求める）。"""
    book = Workbook()
    sheet = book.active
    sheet.title = "評価リクエスト"
    sheet.append(["質問"])
    sheet.append(["合成の質問"])
    sheet.column_dimensions["B"].min = 2
    sheet.column_dimensions["B"].max = 20
    sheet.column_dimensions["B"].width = 9
    output = BytesIO()
    book.save(output)

    result = append_columns(
        output.getvalue(),
        sheet_title="評価リクエスト",
        columns=[AppendedColumn("結果", width=30), AppendedColumn("点", width=31)],
        rows={2: ["A", "B"]},
    )
    root = etree.fromstring(_entries(result)["xl/worksheets/sheet1.xml"])
    spans = [(int(item.get("min")), int(item.get("max"))) for item in root.iter("{*}col")]
    assert spans == [(2, 2), (3, 3), (4, 20)]
    assert load_workbook(BytesIO(result))["評価リクエスト"].column_dimensions["B"].width == 30


def test_column_is_wide_enough_for_its_header() -> None:
    """元の行の高さを変えないので、見出しが切れない幅にする。"""
    header = "評価結果（主条件）: 回答の正確性"
    result = append_columns(
        _workbook(),
        sheet_title="評価リクエスト",
        columns=[AppendedColumn(header, kind="percent", width=12)],
        rows={2: [0.5]},
    )
    sheet = load_workbook(BytesIO(result))["評価リクエスト"]
    assert sheet.column_dimensions["D"].width >= 2 * len(header) - 2


def test_styled_empty_cells_on_the_right_do_not_push_the_columns_away() -> None:
    """値の無い書式だけのセルは使っている範囲に数えず、その位置に値を入れる。"""
    book = Workbook()
    sheet = book.active
    sheet.title = "評価リクエスト"
    sheet.append(["質問"])
    sheet.append(["合成の質問"])
    sheet["D2"].fill = PatternFill("solid", fgColor="00FF00")
    sheet["Z2"].fill = PatternFill("solid", fgColor="00FF00")
    output = BytesIO()
    book.save(output)

    result = append_columns(
        output.getvalue(),
        sheet_title="評価リクエスト",
        columns=[AppendedColumn("結果")],
        rows={2: ["A"]},
    )
    new = load_workbook(BytesIO(result))["評価リクエスト"]
    assert new["B2"].value == "A"
    assert new["Z2"].fill.fgColor.rgb.endswith("00FF00")


def test_row_that_does_not_exist_is_created_in_order() -> None:
    result = append_columns(
        _workbook(),
        sheet_title="評価リクエスト",
        columns=[AppendedColumn("結果")],
        rows={6: ["F"], 9: ["I"]},
    )
    sheet = load_workbook(BytesIO(result))["評価リクエスト"]
    assert sheet["D6"].value == "F"
    assert sheet["D9"].value == "I"
    assert sheet["C8"].value == "表の外の注記"


def test_unknown_sheet_or_broken_file_is_reported() -> None:
    with pytest.raises(XlsxAppendError):
        append_columns(
            _workbook(), sheet_title="無いシート", columns=[AppendedColumn("結果")], rows={}
        )
    with pytest.raises(XlsxAppendError):
        append_columns(
            b"not a zip", sheet_title="評価リクエスト", columns=[AppendedColumn("結果")], rows={}
        )


def test_external_entities_in_the_sheet_are_not_resolved() -> None:
    """DOCTYPE の外部実体を読まない（XXE を防ぐ）。"""
    data = _workbook()
    output = BytesIO()
    with zipfile.ZipFile(BytesIO(data)) as src, zipfile.ZipFile(output, "w") as dst:
        for item in src.infolist():
            content = src.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                content = content.replace(
                    b"<worksheet",
                    b'<!DOCTYPE w [<!ENTITY x SYSTEM "file:///etc/hostname">]><worksheet',
                    1,
                ).replace(
                    b"<sheetData>",
                    b'<sheetData><row r="99"><c r="A99" t="inlineStr"><is><t>&x;</t></is></c>'
                    b"</row>",
                    1,
                )
            dst.writestr(item.filename, content)
    try:
        result = append_columns(
            output.getvalue(),
            sheet_title="評価リクエスト",
            columns=[AppendedColumn("結果")],
            rows={2: ["A"]},
        )
    except XlsxAppendError:
        return
    assert b"hostname" not in _entries(result)["xl/worksheets/sheet1.xml"].replace(
        b"file:///etc/hostname", b""
    )


# ---- 評価の結果の列 ----


def test_results_follow_the_row_number_even_when_questions_repeat() -> None:
    """同じ質問の行が複数あっても、ケースの ID（行番号）で行に戻す。文字の照合はしない。"""
    metrics = _metrics(_case("row-3", score=0.25), _case("row-2", score=0.75))
    result = evaluation_workbook_with_results(
        _workbook(), metrics, {"trace-row-2": "回答 A", "trace-row-3": "回答 B"}
    )
    sheet = load_workbook(BytesIO(result))["評価リクエスト"]
    columns = _headers(sheet)
    answer = columns["評価結果: 回答"]
    correctness = columns["評価結果: 回答の正確性"]
    assert sheet.cell(2, answer).value == "回答 A"
    assert sheet.cell(2, correctness).value == 0.75
    assert sheet.cell(2, correctness).number_format == "0.0%"
    assert sheet.cell(3, answer).value == "回答 B"
    assert sheet.cell(3, correctness).value == 0.25
    # 網羅・裏付けの列は出さない（#4781）。
    assert not [name for name in columns if "網羅" in name or "裏付け" in name]
    assert sheet.cell(2, columns["評価結果: 応答の判断"]).value == "回答"
    assert sheet.cell(2, columns["評価結果: 状態"]).value == "成功"
    assert sheet.cell(2, columns["評価結果: Trace"]).value == "trace-row-2"


def test_rows_without_results_are_marked_not_run_in_a_partial_result() -> None:
    result = evaluation_workbook_with_results(_workbook(), _metrics(_case("row-2")), {})
    sheet = load_workbook(BytesIO(result))["評価リクエスト"]
    columns = _headers(sheet)
    assert sheet.cell(2, columns["評価結果: 状態"]).value == "成功"
    assert sheet.cell(2, columns["評価結果: 回答"]).value == "（回答の記録がありません）"
    assert sheet.cell(3, columns["評価結果: 状態"]).value == "未実行"
    assert sheet.cell(3, columns["評価結果: 回答"]).value is None
    assert sheet.cell(5, columns["評価結果: 状態"]).value == "未実行"
    # 空の行・表の外の行には書かない。
    assert sheet.cell(4, columns["評価結果: 状態"]).value is None
    assert sheet.cell(8, columns["評価結果: 状態"]).value is None


def test_failed_case_shows_reason_and_unassessed_metrics() -> None:
    failed = _case(
        "row-2",
        score=None,
        status="error",
        failure_reasons=["case_error"],
        error_message="合成の失敗",
    )
    sheet = load_workbook(
        BytesIO(evaluation_workbook_with_results(_workbook(), _metrics(failed), {}))
    )["評価リクエスト"]
    columns = _headers(sheet)
    assert sheet.cell(2, columns["評価結果: 状態"]).value == "失敗"
    assert sheet.cell(2, columns["評価結果: 回答"]).value is None
    assert sheet.cell(2, columns["評価結果: 回答の正確性"]).value == "未評価"
    assert sheet.cell(2, columns["評価結果: 失敗理由"]).value == "ケースの実行に失敗"
    assert sheet.cell(2, columns["評価結果: エラー"]).value == "合成の失敗"


def test_comparison_adds_columns_for_both_profiles() -> None:
    metrics = _metrics(_case("row-2"))
    metrics.comparison = EvaluationComparison(
        search_answer_profile_id="profile-b",
        metrics=_metrics(_case("row-2", score=0.125)),
    )
    sheet = load_workbook(
        BytesIO(evaluation_workbook_with_results(_workbook(), metrics, {"trace-row-2": "回答"}))
    )["評価リクエスト"]
    columns = _headers(sheet)
    assert sheet.cell(2, columns["評価結果（主条件）: 回答の正確性"]).value == 0.5
    assert sheet.cell(2, columns["評価結果（比較対象）: 回答の正確性"]).value == 0.125


# ---- API ----


@pytest.fixture
def owner() -> Iterator[AuditRequestContext]:
    context = AuditRequestContext(tenant_id_hash="t" * 64, user_id_hash="u" * 64)
    token = set_audit_request_context(context)
    yield context
    reset_audit_request_context(token)


class _Storage:
    """保存先の代わり（ファイルの内容をメモリに持つ）。"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_get = False
        self.fail_put = False

    async def put(self, key: str, data: bytes, content_type: str) -> str:
        if self.fail_put:
            raise RuntimeError("保存先の障害")
        assert content_type == _XLSX
        self.objects[key] = data
        return f"local://{key}"

    async def get(self, key: str) -> bytes:
        if self.fail_get:
            raise FileNotFoundError(key)
        return self.objects[key.removeprefix("local://")]

    async def delete(self, key: str) -> bool:
        return self.objects.pop(key.removeprefix("local://"), None) is not None


@pytest.fixture
def workbooks() -> Iterator[tuple[EvaluationSourceWorkbookService, _Storage]]:
    storage = _Storage()
    service = EvaluationSourceWorkbookService(
        MemoryEvaluationSourceWorkbookStore(), storage=storage
    )
    set_evaluation_source_workbook_service(service)
    yield service, storage
    set_evaluation_source_workbook_service(None)


@pytest.fixture
def job_service() -> Iterator[EvaluationJobService]:
    installed = set_evaluation_job_service(
        EvaluationJobService(
            MemoryEvaluationJobStore(),
            heartbeat_seconds=0.01,
            worker_id="test-worker",
            finish_retry_seconds=(0.0,),
        )
    )
    yield installed
    set_evaluation_job_service(None)


@pytest.fixture
async def api_client(job_service: EvaluationJobService) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", headers=TEST_REQUEST_HEADERS
    ) as client:
        yield client


async def test_same_file_is_stored_once_per_owner(
    owner: AuditRequestContext, workbooks: tuple[EvaluationSourceWorkbookService, _Storage]
) -> None:
    service, storage = workbooks
    data = _workbook()
    first = await service.save("結果 A.xlsx", data)
    second = await service.save("結果 A.xlsx", data)
    assert first is not None and first == second
    assert len(storage.objects) == 1
    loaded = await service.load(first)
    assert loaded.file_name == "結果 A.xlsx"
    assert loaded.content == data


async def test_another_user_cannot_load_or_reuse_the_workbook(
    owner: AuditRequestContext, workbooks: tuple[EvaluationSourceWorkbookService, _Storage]
) -> None:
    service, _storage = workbooks
    workbook_id = await service.save("結果 A.xlsx", _workbook())
    assert workbook_id is not None

    token = set_audit_request_context(
        AuditRequestContext(tenant_id_hash="t" * 64, user_id_hash="x" * 64)
    )
    try:
        with pytest.raises(evaluation_source_workbooks.EvaluationSourceWorkbookNotFoundError):
            await service.load(workbook_id)
        assert await service.save("結果 A.xlsx", _workbook()) != workbook_id
    finally:
        reset_audit_request_context(token)


async def test_storage_failure_on_save_returns_no_id_and_does_not_log_the_name(
    owner: AuditRequestContext,
    workbooks: tuple[EvaluationSourceWorkbookService, _Storage],
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, storage = workbooks
    storage.fail_put = True
    with caplog.at_level("WARNING"):
        assert await service.save("機微な名前.xlsx", _workbook()) is None
    assert "機微な名前" not in caplog.text


async def test_unreferenced_workbooks_are_removed_after_the_retention(
    owner: AuditRequestContext, workbooks: tuple[EvaluationSourceWorkbookService, _Storage]
) -> None:
    """評価に使われなかった検証だけのファイルは、期限の後で消す。job が使うものは残す。"""
    service, storage = workbooks
    unused = await service.save("未使用.xlsx", _workbook())
    used_book = Workbook()
    used_book.active.append(["質問"])
    output = BytesIO()
    used_book.save(output)
    used = await service.save("使用.xlsx", output.getvalue())
    assert unused and used
    service.store.mark_used(used)  # type: ignore[attr-defined]

    assert await service.purge_unreferenced(older_than_hours=0) == 1
    assert len(storage.objects) == 1
    with pytest.raises(evaluation_source_workbooks.EvaluationSourceWorkbookNotFoundError):
        await service.load(unused)
    assert (await service.load(used)).file_name == "使用.xlsx"


def _fake_runner(monkeypatch: pytest.MonkeyPatch, *, fail_after: int | None = None) -> None:
    async def fake_run(self: EvaluationRunner, **kwargs: Any) -> Any:
        results = []
        for index, case in enumerate(kwargs["cases"]):
            if fail_after is not None and index >= fail_after:
                raise RuntimeError("合成の失敗")
            result = _case(case.id)
            await kwargs["on_case_result"](case_index=index, result=result)
            results.append(result)
        return _metrics(*results)

    async def manifest(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    async def fake_save(self: OracleClient, artifact: Any) -> str:
        return "artifact-1"

    async def answers(self: OracleClient, trace_ids: list[str]) -> dict[str, str]:
        return {trace_id: f"合成の回答 {trace_id}" for trace_id in trace_ids}

    monkeypatch.setattr(EvaluationRunner, "run", fake_run)
    monkeypatch.setattr("app.api.routes.evaluation.build_run_manifest", manifest)
    monkeypatch.setattr(OracleClient, "save_evaluation_artifact", fake_save)
    monkeypatch.setattr(OracleClient, "evaluation_answers_by_trace_ids", answers)


def _run_body(workbook_id: str | None, *case_ids: str) -> dict[str, Any]:
    return {
        "cases": [
            {"id": case_id, "query": "合成の質問", "relevant_document_ids": []}
            for case_id in case_ids
        ],
        "top_k": 5,
        **({"source_workbook_id": workbook_id} if workbook_id else {}),
    }


async def _test_owner_workbook(
    workbooks: tuple[EvaluationSourceWorkbookService, _Storage],
) -> str:
    workbook_id = await workbooks[0].save("評価 A.xlsx", _workbook())
    assert workbook_id is not None
    return workbook_id


@pytest.fixture
def local_owner(monkeypatch: pytest.MonkeyPatch) -> dict[str, tuple[str | None, str | None]]:
    """API の要求の利用者を、元の Excel の持ち主として固定する（他人の場面は値を変える）。"""
    current: dict[str, tuple[str | None, str | None]] = {"owner": ("t" * 64, "u" * 64)}
    monkeypatch.setattr(
        EvaluationSourceWorkbookService, "_owner", staticmethod(lambda: current["owner"])
    )
    return current


async def test_export_uses_the_uploaded_workbook_and_names_the_file(
    api_client: httpx.AsyncClient,
    job_service: EvaluationJobService,
    workbooks: tuple[EvaluationSourceWorkbookService, _Storage],
    local_owner: dict[str, tuple[str | None, str | None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_runner(monkeypatch)
    workbook_id = await _test_owner_workbook(workbooks)
    job = (
        await api_client.post(
            "/api/evaluation/jobs/run", json=_run_body(workbook_id, "row-2", "row-3", "row-5")
        )
    ).json()["data"]
    assert job["source_workbook_id"] == workbook_id
    await job_service.wait_for(job["job_id"])

    response = await api_client.post(f"/api/evaluation/jobs/{job['job_id']}/results.xlsx", json={})
    assert response.status_code == 200
    assert response.headers["x-evaluation-results-source"] == "uploaded"
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    assert "%E8%A9%95%E4%BE%A1%20A_" in response.headers["content-disposition"]
    book = load_workbook(BytesIO(response.content))
    assert book.sheetnames == ["評価リクエスト", "別のシート", "空のシート"]
    sheet = book["評価リクエスト"]
    columns = _headers(sheet)
    assert sheet.cell(3, columns["評価結果: 回答"]).value == "合成の回答 trace-row-3"
    assert sheet.cell(3, 3).value == "メモ B"


async def test_partial_export_marks_the_remaining_rows(
    api_client: httpx.AsyncClient,
    job_service: EvaluationJobService,
    workbooks: tuple[EvaluationSourceWorkbookService, _Storage],
    local_owner: dict[str, tuple[str | None, str | None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_runner(monkeypatch, fail_after=1)
    workbook_id = await _test_owner_workbook(workbooks)
    job = (
        await api_client.post(
            "/api/evaluation/jobs/run", json=_run_body(workbook_id, "row-2", "row-3", "row-5")
        )
    ).json()["data"]
    await job_service.wait_for(job["job_id"])

    done = (await api_client.get(f"/api/evaluation/jobs/{job['job_id']}")).json()["data"]
    assert done["status"] == "FAILED"
    response = await api_client.post(f"/api/evaluation/jobs/{job['job_id']}/results.xlsx", json={})
    assert response.headers["x-evaluation-results-source"] == "uploaded"
    sheet = load_workbook(BytesIO(response.content))["評価リクエスト"]
    columns = _headers(sheet)
    assert sheet.cell(2, columns["評価結果: 状態"]).value == "成功"
    assert sheet.cell(3, columns["評価結果: 状態"]).value == "未実行"


async def test_job_without_workbook_exports_the_results_only_file(
    api_client: httpx.AsyncClient,
    job_service: EvaluationJobService,
    workbooks: tuple[EvaluationSourceWorkbookService, _Storage],
    local_owner: dict[str, tuple[str | None, str | None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API で直接投入した job は元の Excel が無いので、結果だけのファイルにする。"""
    _fake_runner(monkeypatch)
    job = (
        await api_client.post("/api/evaluation/jobs/run", json=_run_body(None, "case-a"))
    ).json()["data"]
    assert job["source_workbook_id"] is None
    await job_service.wait_for(job["job_id"])

    response = await api_client.post(f"/api/evaluation/jobs/{job['job_id']}/results.xlsx", json={})
    assert response.status_code == 200
    assert response.headers["x-evaluation-results-source"] == "generated"
    assert "evaluation_results.xlsx" in response.headers["content-disposition"]
    assert load_workbook(BytesIO(response.content)).sheetnames == ["概要", "主条件の結果"]


async def test_export_falls_back_to_results_only_when_the_workbook_cannot_be_read(
    api_client: httpx.AsyncClient,
    job_service: EvaluationJobService,
    workbooks: tuple[EvaluationSourceWorkbookService, _Storage],
    local_owner: dict[str, tuple[str | None, str | None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_runner(monkeypatch)
    workbook_id = await _test_owner_workbook(workbooks)
    job = (
        await api_client.post("/api/evaluation/jobs/run", json=_run_body(workbook_id, "row-2"))
    ).json()["data"]
    await job_service.wait_for(job["job_id"])
    workbooks[1].fail_get = True

    response = await api_client.post(f"/api/evaluation/jobs/{job['job_id']}/results.xlsx", json={})
    assert response.status_code == 200
    assert response.headers["x-evaluation-results-source"] == "generated"


async def test_submit_rejects_a_workbook_of_another_user(
    api_client: httpx.AsyncClient,
    job_service: EvaluationJobService,
    workbooks: tuple[EvaluationSourceWorkbookService, _Storage],
    local_owner: dict[str, tuple[str | None, str | None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_runner(monkeypatch)
    local_owner["owner"] = ("t" * 64, "x" * 64)
    other = await workbooks[0].save("他人.xlsx", _workbook())
    assert other is not None
    local_owner["owner"] = ("t" * 64, "u" * 64)

    response = await api_client.post("/api/evaluation/jobs/run", json=_run_body(other, "row-2"))
    assert response.status_code == 404


async def test_submit_rejects_cases_that_are_not_rows_of_the_workbook(
    api_client: httpx.AsyncClient,
    job_service: EvaluationJobService,
    workbooks: tuple[EvaluationSourceWorkbookService, _Storage],
    local_owner: dict[str, tuple[str | None, str | None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """行に戻せないケースは受けない。"""
    _fake_runner(monkeypatch)
    workbook_id = await _test_owner_workbook(workbooks)
    # 空の行・無い行・行番号でない ID。
    for case_id in ("row-4", "row-99", "case-a"):
        response = await api_client.post(
            "/api/evaluation/jobs/run", json=_run_body(workbook_id, case_id)
        )
        assert response.status_code == 422
