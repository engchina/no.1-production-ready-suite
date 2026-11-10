"""合成の入力を保存・確認・Excel 再取込し、実ランナーで評価する受入回帰。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from app.api.routes import evaluation as evaluation_routes
from app.clients.evaluation_testset_store import MemoryTestSetStore
from app.clients.oracle import OracleClient
from app.main import app
from app.rag import evaluation_testsets
from app.rag.evaluation import EvaluationRunner
from app.rag.evaluation_excel import (
    EvaluationDocumentRef,
    EvaluationTemplateRow,
    evaluation_cases_xlsx,
    parse_evaluation_workbook,
)
from app.rag.evaluation_jobs import (
    EvaluationJobService,
    MemoryEvaluationJobStore,
    set_evaluation_job_service,
)
from app.rag.evaluation_source_workbooks import (
    EvaluationSourceWorkbookService,
    MemoryEvaluationSourceWorkbookStore,
    set_evaluation_source_workbook_service,
)
from app.rag.evaluation_testsets import OracleTestSetSourceReader
from app.rag.pipeline import SearchStageProgressCallback
from app.rag.request_context import (
    audit_request_context_from_headers,
    reset_audit_request_context,
    set_audit_request_context,
)
from app.schemas.evaluation_testsets import TestSetEdit as DraftEdit
from app.schemas.search import RetrievedChunk, SearchDiagnostics, SearchRequest, SearchResponse
from tests.support import TEST_REQUEST_HEADERS
from tests.test_evaluation_results_workbook import _Storage

BASE = "/api/evaluation/testsets"
QUESTION = "合成の値と手順を教えてください。"
STANDARD = "合成の値は12です。"
EVIDENCE = [
    "合成資料.pdf | 文書ID: doc-a | p.2-4 | 章節: 合成章 | 抜粋: 合成の本文",
    "合成表.xlsx | 文書ID: doc-b | シート: 表A | 行: 10-12 | 抜粋: 合成の値",
]


@contextmanager
def audit_context() -> Iterator[None]:
    # ASGI の Headers と同じ小文字の名前で保存時の tenant をそろえる。
    context = audit_request_context_from_headers(
        {name.lower(): value for name, value in TEST_REQUEST_HEADERS.items()},
        request_id="synthetic-testset-flow",
    )
    token = set_audit_request_context(context)
    try:
        yield
    finally:
        reset_audit_request_context(token)


def workbook(standard: str = STANDARD) -> bytes:
    return evaluation_cases_xlsx(
        [
            EvaluationTemplateRow(
                question=QUESTION,
                evidence=EVIDENCE,
                standard_answer=standard,
                required_answer_points=["合成の要点"],
                response_decision="answer",
                answer_process="合成の確認をしてから保存する。",
            )
        ]
    )


class SourceReader(OracleTestSetSourceReader):
    """job と回答だけは合成。rows は本物の保存サービスと Excel reader を使う。"""

    def __init__(self) -> None:
        super().__init__()
        self.jobs: dict[str, dict[str, Any]] = {}
        self.answers: dict[str, dict[str, Any]] = {}

    async def list_jobs(self) -> list[dict[str, Any]]:
        return [dict(job_id=key, total_cases=1) for key in self.jobs]

    async def job(self, job_id: str) -> Mapping[str, Any] | None:
        return deepcopy(self.jobs.get(job_id))

    async def answer(self, trace_id: str) -> Mapping[str, Any] | None:
        return deepcopy(self.answers.get(trace_id))

    def add_job(self, job_id: str, workbook_id: str) -> None:
        trace_id = "trace-" + job_id
        self.jobs[job_id] = {
            "source_workbook_id": workbook_id,
            "result": {"case_results": [{"case_id": "row-2", "trace_id": trace_id}]},
        }
        self.answers[trace_id] = {
            "question": "生成側の文では元の質問を上書きしない。",
            "answer": "未確認の参考回答",
            "citations_json": [{"document_id": "doc-a", "text": "参考の原文"}],
        }


class SyntheticPipeline:
    """外部検索・LLM の代わり。ランナーの実行・採点・進捗・job 保存は本物。"""

    def __init__(self) -> None:
        self.requests: list[SearchRequest] = []

    async def run(
        self,
        request: SearchRequest,
        trace_id: str | None = None,
        progress_callback: SearchStageProgressCallback | None = None,
    ) -> SearchResponse:
        assert trace_id
        self.requests.append(request)
        answer = STANDARD + "合成の要点"
        return SearchResponse(
            answer=answer,
            trace_id=trace_id,
            elapsed_ms=1,
            diagnostics=SearchDiagnostics(answer={"outcome": "answered"}),
            citations=[
                RetrievedChunk(
                    document_id="doc-a",
                    chunk_id="chunk-a",
                    text="合成の本文",
                    score=1,
                    metadata={"page_start": 3, "page_end": 3, "section_path": ["合成章"]},
                ),
                RetrievedChunk(
                    document_id="doc-b",
                    chunk_id="chunk-b",
                    text="合成の値",
                    score=1,
                    metadata={
                        "sheet_name": "表A",
                        "row_start": 10,
                        "row_end": 12,
                        "row_basis": "sheet",
                    },
                ),
            ],
        ).with_evaluation_input({"question": request.query, "answer_text": answer})


class SyntheticJudge:
    """外部の意味判定だけを固定し、どの標準回答を渡したかを記録する。"""

    def __init__(self) -> None:
        self.standards: list[str] = []

    async def __call__(
        self,
        *,
        trace_id: str,
        evaluation_input: Mapping[str, object],
        standard_answer: str,
        timeout_seconds: float,
        judge_model_id: str,
    ) -> Mapping[str, object]:
        self.standards.append(standard_answer)
        return {
            "status": "completed",
            "passed": True,
            "answer_correctness": 1.0,
        }


@dataclass
class Flow:
    client: httpx.AsyncClient
    reader: SourceReader
    drafts: MemoryTestSetStore
    storage: _Storage
    jobs: EvaluationJobService
    pipeline: SyntheticPipeline
    judge: SyntheticJudge
    artifacts: list[dict[str, Any]]


@pytest.fixture
async def flow(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Flow]:
    reader, drafts, storage = SourceReader(), MemoryTestSetStore(), _Storage()
    workbooks = EvaluationSourceWorkbookService(
        MemoryEvaluationSourceWorkbookStore(), storage=storage
    )
    jobs = EvaluationJobService(
        MemoryEvaluationJobStore(),
        heartbeat_seconds=0.01,
        finish_retry_seconds=(0,),
    )
    set_evaluation_source_workbook_service(workbooks)
    set_evaluation_job_service(jobs)
    monkeypatch.setattr(evaluation_testsets, "_STORE", drafts)
    monkeypatch.setattr(evaluation_testsets, "_READER", reader)
    pipeline, judge = SyntheticPipeline(), SyntheticJudge()
    runner = EvaluationRunner(pipeline=pipeline, answer_judge=judge)
    # factory のみを差し替え、EvaluationRunner.run / _run は本物のまま呼ぶ。
    monkeypatch.setattr(evaluation_routes, "EvaluationRunner", lambda: runner)
    artifacts: list[dict[str, Any]] = []

    async def documents(kb_ids: Sequence[str]) -> list[EvaluationDocumentRef]:
        assert list(kb_ids) == ["kb-a"]
        return [
            EvaluationDocumentRef("doc-a", "合成資料.pdf", page_count=10),
            EvaluationDocumentRef("doc-b", "合成表.xlsx", sheet_names=("表A",)),
        ]

    async def manifest(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"synthetic_fixture": True}

    async def save_artifact(self: OracleClient, payload: dict[str, Any]) -> str:
        artifacts.append(deepcopy(payload))
        return "synthetic-evaluation-artifact"

    monkeypatch.setattr(evaluation_routes, "_knowledge_base_documents", documents)
    monkeypatch.setattr(evaluation_routes, "build_run_manifest", manifest)
    monkeypatch.setattr(OracleClient, "save_evaluation_artifact", save_artifact)

    async def prompt_overrides(self: OracleClient) -> dict[str, str]:
        return {}

    monkeypatch.setattr(OracleClient, "answer_prompt_overrides", prompt_overrides)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers=TEST_REQUEST_HEADERS,
        ) as client:
            yield Flow(client, reader, drafts, storage, jobs, pipeline, judge, artifacts)
    finally:
        await jobs.shutdown()
        set_evaluation_job_service(None)
        set_evaluation_source_workbook_service(None)


async def import_bytes(client: httpx.AsyncClient, content: bytes) -> dict[str, Any]:
    response = await client.post(
        "/api/evaluation/cases/import",
        data={"knowledge_base_ids": ["kb-a"]},
        files={
            "file": (
                "synthetic.xlsx",
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    assert data["error_count"] == 0, data
    assert data["source_workbook_id"]
    return data


async def create_snapshot(flow: Flow, *, two_jobs: bool = False) -> dict[str, Any]:
    for job_id, standard in [
        ("job-a", STANDARD),
        *([("job-b", "別の合成の標準回答")] if two_jobs else []),
    ]:
        imported = await import_bytes(flow.client, workbook(standard))
        flow.reader.add_job(job_id, imported["source_workbook_id"])
    job_ids = list(flow.reader.jobs)
    response = await flow.client.post(BASE + "/sources/items", json={"job_ids": job_ids})
    assert response.status_code == 200, response.text
    candidates = response.json()["data"]
    assert len(candidates) == len(job_ids)
    response = await flow.client.post(
        BASE,
        json={
            "name": "合成のテストセット",
            "job_ids": job_ids,
            "item_ids": [item["item_id"] for item in candidates],
        },
    )
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()["data"]
    return result


async def confirm_first(flow: Flow, draft: dict[str, Any]) -> dict[str, Any]:
    edits = [
        {key: value for key, value in row.items() if key in DraftEdit.model_fields}
        for row in draft["items"]
    ]
    edits[0]["confirmed"] = True
    response = await flow.client.put(
        BASE + "/" + draft["draft_id"],
        json={
            "name": draft["name"],
            "revision": draft["revision"],
            "items": edits,
        },
    )
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()["data"]
    return result


async def test_workbook_snapshot_preserves_six_columns_and_survives_source_deletion(
    flow: Flow,
) -> None:
    draft = await create_snapshot(flow, two_jobs=True)
    first, second = draft["items"]
    assert first["item_id"] != second["item_id"]
    assert first["question"] == second["question"] == QUESTION
    assert first["source_job_id"] == "job-a" and second["source_job_id"] == "job-b"
    assert first["standard_answer"] == STANDARD
    assert second["standard_answer"] == "別の合成の標準回答"
    assert first["evidence"] == EVIDENCE
    assert first["required_answer_points"] == ["合成の要点"]
    assert first["response_decision"] == "answer"
    assert first["answer_process"] == "合成の確認をしてから保存する。"
    assert not first["confirmed"] and not second["confirmed"]
    assert first["reference_answer"] == "未確認の参考回答"
    assert first["original"]["standard_answer"] == STANDARD
    assert first["reference_evidence"] == [{"document_id": "doc-a", "text": "参考の原文"}]
    confirmed = await confirm_first(flow, draft)
    flow.reader.jobs.clear()
    flow.reader.answers.clear()
    flow.storage.objects.clear()
    reopened = await flow.client.get(BASE + "/" + draft["draft_id"])
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["data"]["items"] == confirmed["items"]
    download = await flow.client.get(
        BASE + "/" + draft["draft_id"] + "/export.xlsx", params={"revision": 2}
    )
    assert download.status_code == 200, download.text
    parsed = parse_evaluation_workbook(download.content)
    assert not parsed.issues and len(parsed.rows) == 1
    row = parsed.rows[0]
    assert row.question == QUESTION and row.standard_answer == STANDARD
    assert [spec.line for spec in row.evidence] == EVIDENCE
    assert row.screen_lines == ("合成の要点",) and row.response_decision == "answer"
    assert row.answer_process == "合成の確認をしてから保存する。"


async def test_export_unmodified_import_and_real_runner_job_completion(flow: Flow) -> None:
    draft = await confirm_first(flow, await create_snapshot(flow))
    download = await flow.client.get(
        BASE + "/" + draft["draft_id"] + "/export.xlsx", params={"revision": draft["revision"]}
    )
    assert download.status_code == 200, download.text
    # 出力の bytes を編集せず、既存 reader と投入 API にそのまま渡す。
    imported = await import_bytes(flow.client, download.content)
    case = imported["cases"][0]
    assert case["standard_answer"] == STANDARD and case["required_answer_points"] == ["合成の要点"]
    assert case["expected_response_decision"] == "answer"
    assert case["required_evidence"][0]["page_start"] == 2
    assert case["required_evidence"][0]["page_end"] == 4
    assert case["required_evidence"][1]["sheet"] == "表A"
    assert case["required_evidence"][1]["row_end"] == 12
    assert imported["rows"][0]["answer_process"] == "合成の確認をしてから保存する。"
    assert flow.pipeline.requests == []
    submitted = await flow.client.post(
        "/api/evaluation/jobs/run",
        json={
            "cases": imported["cases"],
            "knowledge_base_ids": ["kb-a"],
            "source_workbook_id": imported["source_workbook_id"],
            "suite": "standard",
        },
    )
    assert submitted.status_code == 202, submitted.text
    job_id = submitted.json()["data"]["job_id"]
    await flow.jobs.wait_for(job_id)
    completed = await flow.client.get("/api/evaluation/jobs/" + job_id)
    assert completed.status_code == 200, completed.text
    job = completed.json()["data"]
    assert job["status"] == "SUCCEEDED", job
    assert job["completed_cases"] == 1 and job["total_cases"] == 1
    assert len(flow.pipeline.requests) == 1 and flow.pipeline.requests[0].query == QUESTION
    assert flow.judge.standards == [STANDARD + "\n合成の要点"]
    result = job["run_result"]
    assert result["case_count"] == 1 and result["error_count"] == 0
    assert result["required_evidence_recall"] == result["mrr"] == 1.0
    assert result["answer_correctness"] == 1.0
    assert result["response_decision_accuracy"] == 1.0
    assert result["case_results"][0]["status"] == "success"
    assert len(flow.artifacts) == 1
    assert flow.artifacts[0]["result_summary"]["case_count"] == 1
