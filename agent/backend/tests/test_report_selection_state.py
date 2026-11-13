"""最終選択肢が同じ実行の公開可能な回答案だけに対応することを確かめる。"""

from copy import deepcopy

import pytest

from app.features.agent import run_claim
from app.features.agent.report_contract import (
    AnswerBudget,
    AnswerPlan,
    NarrativeBlock,
    ReportOutputContract,
)
from app.features.agent.report_preview import (
    RenderedBlockCost,
    RenderedReport,
    ReportPreviewError,
    create_preview,
    selectable_previews,
)
from app.features.agent.report_runtime import source_digest
from app.features.agent.runtime import (
    AgentProfile,
    AgentProfilePatch,
    AgentRuntimeRepository,
    RunCreateRequest,
)
from app.features.agent.tools import ToolCall, ToolResult


def rendered(text: str, digest: str) -> RenderedReport:
    return RenderedReport(
        body=text,
        block_costs=(RenderedBlockCost(index=1, kind="narrative", expanded_codepoints=len(text)),),
        source_digest=digest,
    )


def test_candidates_exclude_hard_limit_and_stale_sources_without_reusing_ids() -> None:
    contract = ReportOutputContract(budget=AnswerBudget(soft_target=1, hard_max=8))
    ledger = None
    for number, (text, digest) in enumerate(
        [("長い合成の本文です。", "a" * 64), ("短文", "a" * 64), ("更新文", "b" * 64)], 1
    ):
        output = rendered(text, digest)

        def render(_: AnswerPlan, value: RenderedReport = output) -> RenderedReport:
            return value

        created = create_preview(
            ledger,
            run_id="run-a",
            operation_key=f"operation-{number}",
            plan=AnswerPlan(blocks=(NarrativeBlock(text=text),)),
            contract=contract,
            render_body=render,
        )
        ledger = created.ledger
    assert selectable_previews(
        ledger, run_id="run-a", contract=contract, source_digest="a" * 64
    ) == ("A2",)
    assert selectable_previews(
        ledger, run_id="run-a", contract=contract, source_digest="b" * 64
    ) == ("A3",)
    assert (
        selectable_previews(ledger, run_id="run-a", contract=contract, source_digest="c" * 64) == ()
    )
    assert ledger is not None
    assert [item["preview_id"] for item in ledger["records"]] == ["A1", "A2", "A3"]


def test_candidate_lookup_rejects_foreign_contract_and_corrupt_ledger() -> None:
    contract = ReportOutputContract()
    created = create_preview(
        None,
        run_id="run-a",
        operation_key="operation-a",
        plan=AnswerPlan(blocks=(NarrativeBlock(text="合成の本文"),)),
        contract=contract,
        render_body=lambda _: rendered("合成の本文", "a" * 64),
    )
    with pytest.raises(ReportPreviewError, match="この実行"):
        selectable_previews(
            created.ledger, run_id="run-b", contract=contract, source_digest="a" * 64
        )
    with pytest.raises(ReportPreviewError, match="出力設定"):
        selectable_previews(
            created.ledger,
            run_id="run-a",
            contract=ReportOutputContract(budget=AnswerBudget(soft_target=1)),
            source_digest="a" * 64,
        )
    broken = deepcopy(created.ledger)
    broken["records"][0]["body"] = "改変された本文"
    with pytest.raises(ReportPreviewError):
        selectable_previews(broken, run_id="run-a", contract=contract, source_digest="a" * 64)


def test_repository_choices_follow_actual_state_and_do_not_write_on_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = AgentRuntimeRepository()
    contract = ReportOutputContract()
    repository.create_agent(AgentProfile(id="report-a", name="合成Agent", output_contract=contract))
    run = repository.create_builtin_run(RunCreateRequest(agent_id="report-a", goal="説明する"))
    repository.begin_builtin_run(run.id)
    before = repository.get_run(run.id)
    with monkeypatch.context() as scoped:
        scoped.setattr(repository, "_persist_locked", lambda: pytest.fail("候補の読取りで保存した"))
        assert repository.report_selection_ids(run.id, contract) == ()
    assert repository.get_run(run.id) == before
    repository.register_report_preview(
        run.id,
        operation_key="operation-a",
        plan=AnswerPlan(blocks=(NarrativeBlock(text="確認済みの本文"),)),
        contract=contract,
        renderer=lambda current, _: rendered("確認済みの本文", source_digest(current)),
    )
    assert repository.report_selection_ids(run.id, contract) == ("A1",)
    stale = deepcopy(before.metadata)
    stale[run_claim.EXECUTION_METADATA_KEY]["token"] = "old-execution-token"
    with (
        run_claim.execution_fence(run.id, stale),
        pytest.raises(ReportPreviewError, match="実行中"),
    ):
        repository.report_selection_ids(run.id, contract)
    assert repository.report_selection_ids(run.id, contract) == ("A1",)
    next_contract = ReportOutputContract(budget=AnswerBudget(hard_max=1))
    repository.patch_agent("report-a", AgentProfilePatch(output_contract=next_contract))
    repository.publish_agent("report-a")
    assert repository.report_selection_ids(run.id, contract) == ("A1",)
    with pytest.raises(ReportPreviewError, match="出力設定"):
        repository.report_selection_ids(run.id, next_contract)

    step, _ = repository.start_builtin_tool_step(
        run.id, ToolCall(name="nl2sql_query", arguments={})
    )
    repository.finish_builtin_tool_step(
        run.id,
        step,
        ToolResult(
            name="nl2sql_query",
            success=True,
            output={"status": "done", "columns": ["VALUE"], "rows": [{"VALUE": 1}]},
        ),
    )
    assert repository.report_selection_ids(run.id, contract) == ()
    repository.cancel_run(run.id)
    with pytest.raises(ReportPreviewError, match="実行中"):
        repository.report_selection_ids(run.id, contract)
