"""同じ描画済み本文を保存・復元し、予算と操作識別子を厳密に保つ。"""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from typing import Any

import pytest

from app.features.agent import report_preview
from app.features.agent.answer_data import DataIndex, DataReferenceError
from app.features.agent.report_contract import (
    AnswerBudget,
    AnswerPlan,
    FinalSelection,
    NarrativeBlock,
    ReportOutputContract,
    TableBlock,
)
from app.features.agent.report_fields import ConfirmationSection
from app.features.agent.report_preview import (
    MAX_REPORT_BODY_CHARS,
    MAX_REPORT_PREVIEWS,
    RenderedBlockCost,
    RenderedReport,
    ReportPreviewError,
    create_preview,
    report_source_digest,
    select_preview,
)

KEY = "a" * 24


def _source() -> DataIndex:
    return DataIndex(
        cells={},
        tables={
            KEY: (
                ["ID", "VALUE"],
                [
                    {"ID": 1, "VALUE": None},
                    {"ID": 2, "VALUE": "日本語🌸e\u0301"},
                    {"ID": 3, "VALUE": "[[table:T999]]"},
                ],
            )
        },
        aliases={"T1": KEY},
    )


def _renderer(
    calls: list[int] | None = None, *, suffix: str = ""
) -> Callable[[AnswerPlan], RenderedReport]:
    index = _source()
    sources = {"[[cite:1]]": "[確認済みの原文](source-path)"}
    snapshot = {"tables": index.tables, "aliases": index.aliases, "sources": sources}

    def render(plan: AnswerPlan) -> RenderedReport:
        if calls is not None:
            calls.append(1)
        parts: list[str] = []
        costs: list[RenderedBlockCost] = []
        for number, block in enumerate(plan.blocks, 1):
            if isinstance(block, TableBlock):
                text = index.render(block.marker())
            elif isinstance(block, NarrativeBlock):
                if "[[cite:2]]" in block.text:
                    raise DataReferenceError("取得していない原文参照")
                text = index.render(block.text.replace("[[cite:1]]", sources["[[cite:1]]"]))
            else:
                text = "確認結果（プログラム描画）"
            parts.append(text)
            costs.append(
                RenderedBlockCost(index=number, kind=block.kind, expanded_codepoints=len(text))
            )
        body = "\n\n".join(parts) + suffix
        return RenderedReport(
            body=body,
            block_costs=tuple(costs),
            composition_delta_chars=len(body) - sum(c.expanded_codepoints for c in costs),
            source_digest=report_source_digest(snapshot),
        )

    return render


def _plan() -> AnswerPlan:
    return AnswerPlan(blocks=(TableBlock(table="T1"),))


def _preview(
    *, contract: ReportOutputContract | None = None, plan: AnswerPlan | None = None
) -> Any:
    return create_preview(
        None,
        run_id="run-a",
        operation_key="op-a",
        plan=plan or _plan(),
        contract=contract or ReportOutputContract(),
        render_body=_renderer(),
    )


def test_reference_expansion_and_source_rendering_are_counted_before_final_freeze() -> None:
    plan = AnswerPlan(
        blocks=(
            NarrativeBlock(text="根拠 [[cite:1]]"),
            TableBlock(table="T1"),
            ConfirmationSection(items=[]),
        )
    )
    calls: list[int] = []
    created = create_preview(
        None,
        run_id="run-a",
        operation_key="op-a",
        plan=plan,
        contract=ReportOutputContract(),
        render_body=_renderer(calls, suffix="\n最終付記"),
    )
    assert created.result.total_codepoints > len("[[table:T1]]")
    assert (
        created.result.total_codepoints
        == sum(c.expanded_codepoints for c in created.result.block_costs)
        + created.result.composition_delta_chars
    )
    final = select_preview(
        created.ledger,
        run_id="run-a",
        selection=FinalSelection(preview_id="A1"),
        contract=ReportOutputContract(),
    )
    assert final.body == _renderer(suffix="\n最終付記")(plan).body
    assert final.body is not None and "[[cite:" not in final.body and "確認結果" in final.body
    assert len(final.body) == created.result.total_codepoints
    assert calls == [1]
    assert not any(
        k in created.result.model_dump()
        for k in ("body", "source_digest", "record_digest", "operation_key")
    )


@pytest.mark.parametrize(
    "budget,status,publish",
    [
        (AnswerBudget(soft_target=1), "OVER_TARGET", True),
        (AnswerBudget(hard_max=1), "REJECTED_HARD_MAX", False),
        (AnswerBudget(soft_target=1, hard_max=10000), "OVER_TARGET", True),
        (AnswerBudget(hard_max=10000), "WITHIN_BUDGET", True),
    ],
)
def test_soft_target_and_explicit_hard_max_have_different_publication(
    budget: AnswerBudget, status: str, publish: bool
) -> None:
    contract = ReportOutputContract(budget=budget)
    created = _preview(contract=contract)
    assert created.result.status == status
    final = select_preview(
        created.ledger, run_id="run-a", selection=FinalSelection(preview_id="A1"), contract=contract
    )
    assert (final.body is not None) == publish
    assert final.status == ("complete" if publish else "incomplete")
    assert final.reason == (None if publish else "hard_max_exceeded")
    assert created.result.incomplete is (not publish)
    if budget.soft_target is not None:
        assert (
            created.result.remaining_target == budget.soft_target - created.result.total_codepoints
        )
    if budget.hard_max is not None:
        assert created.result.remaining_hard == budget.hard_max - created.result.total_codepoints


@pytest.mark.parametrize(
    "rows,cols,layout",
    [("1", "2,1", "horizontal"), ("3,1", "1,2", "horizontal"), ("1-2", "2", "vertical")],
)
def test_null_unicode_and_projection_match_real_dataindex(
    rows: str, cols: str, layout: Any
) -> None:
    table = TableBlock(table="T1", rows=rows, cols=cols, layout=layout)
    created = _preview(plan=AnswerPlan(blocks=(table,)))
    final = select_preview(
        created.ledger,
        run_id="run-a",
        selection=FinalSelection(preview_id="A1"),
        contract=ReportOutputContract(),
    )
    assert final.body == _source().render(table.marker())
    assert len(final.body or "") == created.result.total_codepoints


@pytest.mark.parametrize("rows", ["0", "1,1", "2-1", "1-9999999999999999999"])
def test_invalid_projection_never_trims_or_invents_rows(rows: str) -> None:
    with pytest.raises(DataReferenceError):
        _preview(plan=AnswerPlan(blocks=(TableBlock(table="T1", rows=rows, cols="1"),)))


def test_restored_ledger_preserves_ids_idempotence_and_frozen_body() -> None:
    calls: list[int] = []
    render = _renderer(calls)
    first = create_preview(
        None,
        run_id="run-a",
        operation_key="op-a",
        plan=_plan(),
        contract=ReportOutputContract(),
        render_body=render,
    )
    restored = json.loads(json.dumps(first.ledger, ensure_ascii=False))
    before = deepcopy(restored)
    repeated = create_preview(
        restored,
        run_id="run-a",
        operation_key="op-a",
        plan=_plan(),
        contract=ReportOutputContract(),
        render_body=render,
    )
    assert repeated.result.preview_id == "A1" and repeated.result.reused
    assert calls == [1] and restored == before
    smaller = AnswerPlan(blocks=(TableBlock(table="T1", rows="1", cols="1"),))
    second = create_preview(
        restored,
        run_id="run-a",
        operation_key="op-b",
        plan=smaller,
        contract=ReportOutputContract(),
        render_body=render,
    )
    assert second.result.preview_id == "A2" and len(calls) == 2
    assert (
        select_preview(
            second.ledger,
            run_id="run-a",
            selection=FinalSelection(preview_id="A1"),
            contract=ReportOutputContract(),
        ).body
        == first.ledger["records"][0]["body"]
    )
    assert first.ledger["records"][0]["body"] != second.ledger["records"][1]["body"]


def test_operation_id_conflict_does_not_render_and_source_plan_is_not_modified() -> None:
    created = _preview()
    original = deepcopy(created.ledger)
    calls: list[int] = []
    with pytest.raises(ReportPreviewError) as error:
        create_preview(
            created.ledger,
            run_id="run-a",
            operation_key="op-a",
            plan=AnswerPlan(blocks=(NarrativeBlock(text="other"),)),
            contract=ReportOutputContract(),
            render_body=_renderer(calls),
        )
    assert error.value.code == "runtime.report_operation_conflict" and calls == []
    assert created.ledger == original


@pytest.mark.parametrize(
    "field,value",
    [
        ("body", "tampered"),
        ("source_digest", "b" * 64),
        ("total_codepoints", 1),
        ("remaining_hard", 123),
        ("status", "OVER_TARGET"),
        ("preview_id", "A2"),
        ("plan_digest", "wrong"),
        ("record_digest", "wrong"),
    ],
)
def test_corrupted_saved_preview_cannot_be_selected(field: str, value: Any) -> None:
    created = _preview()
    ledger = deepcopy(created.ledger)
    ledger["records"][0][field] = value
    with pytest.raises(ReportPreviewError):
        select_preview(
            ledger,
            run_id="run-a",
            selection=FinalSelection(preview_id="A1"),
            contract=ReportOutputContract(),
        )


def test_wrong_run_changed_budget_or_unknown_id_has_typed_failure() -> None:
    created = _preview()
    for run_id, contract, preview_id, expected in [
        ("run-b", ReportOutputContract(), "A1", "runtime.report_preview_wrong_run"),
        (
            "run-a",
            ReportOutputContract(budget=AnswerBudget(hard_max=999)),
            "A1",
            "runtime.report_contract_changed",
        ),
        ("run-a", ReportOutputContract(), "A2", "runtime.report_preview_not_found"),
    ]:
        with pytest.raises(ReportPreviewError) as error:
            select_preview(
                created.ledger,
                run_id=run_id,
                selection=FinalSelection(preview_id=preview_id),
                contract=contract,
            )
        assert error.value.code == expected


def test_count_limit_never_evicts_or_renumbers_old_previews() -> None:
    calls: list[int] = []
    ledger = None
    for number in range(MAX_REPORT_PREVIEWS):
        created = create_preview(
            ledger,
            run_id="run-a",
            operation_key=f"op-{number}",
            plan=_plan(),
            contract=ReportOutputContract(),
            render_body=_renderer(calls),
        )
        ledger = created.ledger
    assert ledger is not None and len(calls) == MAX_REPORT_PREVIEWS
    with pytest.raises(ReportPreviewError) as error:
        create_preview(
            ledger,
            run_id="run-a",
            operation_key="op-new",
            plan=_plan(),
            contract=ReportOutputContract(),
            render_body=_renderer(calls),
        )
    assert error.value.code == "runtime.report_preview_limit" and len(calls) == MAX_REPORT_PREVIEWS
    again = create_preview(
        ledger,
        run_id="run-a",
        operation_key="op-0",
        plan=_plan(),
        contract=ReportOutputContract(),
        render_body=_renderer(calls),
    )
    assert again.result.preview_id == "A1" and again.result.reused


def test_ledger_byte_limit_stops_without_updating_input(monkeypatch: pytest.MonkeyPatch) -> None:
    created = _preview()
    original = deepcopy(created.ledger)
    monkeypatch.setattr(report_preview, "MAX_REPORT_LEDGER_BYTES", 10)
    with pytest.raises(ReportPreviewError) as error:
        create_preview(
            created.ledger,
            run_id="run-a",
            operation_key="op-b",
            plan=_plan(),
            contract=ReportOutputContract(),
            render_body=_renderer(),
        )
    assert error.value.code == "runtime.report_preview_limit" and created.ledger == original


@pytest.mark.parametrize("operation", ["", "x" * 257])
def test_missing_or_overlong_operation_key_is_not_accepted(operation: str) -> None:
    with pytest.raises(ReportPreviewError) as error:
        create_preview(
            None,
            run_id="run-a",
            operation_key=operation,
            plan=_plan(),
            contract=ReportOutputContract(),
            render_body=_renderer(),
        )
    assert error.value.code == "runtime.report_operation_key_invalid"


def test_callback_cost_mismatch_and_body_limit_are_typed() -> None:
    def wrong(plan: AnswerPlan) -> RenderedReport:
        return RenderedReport(
            body="x",
            block_costs=(RenderedBlockCost(index=1, kind="table", expanded_codepoints=999),),
            source_digest="a" * 64,
        )

    with pytest.raises(ReportPreviewError, match="文字数"):
        create_preview(
            None,
            run_id="run-a",
            operation_key="op",
            plan=_plan(),
            contract=ReportOutputContract(),
            render_body=wrong,
        )

    def huge(plan: AnswerPlan) -> RenderedReport:
        return RenderedReport(
            body="x" * (MAX_REPORT_BODY_CHARS + 1), block_costs=(), source_digest="a" * 64
        )

    with pytest.raises(ReportPreviewError) as error:
        create_preview(
            None,
            run_id="run-a",
            operation_key="op",
            plan=_plan(),
            contract=ReportOutputContract(),
            render_body=huge,
        )
    assert error.value.code == "runtime.report_render_invalid"


def test_confirmation_mutation_by_callback_cannot_modify_frozen_plan() -> None:
    plan = AnswerPlan(blocks=(ConfirmationSection(items=[]),))

    def mutating(value: AnswerPlan) -> RenderedReport:
        block = value.blocks[0]
        assert isinstance(block, ConfirmationSection)
        from app.features.agent.report_fields import ConfirmationItem

        block.items.append(ConfirmationItem(field_ref="S1:F1"))
        return RenderedReport(
            body="x",
            block_costs=(
                RenderedBlockCost(index=1, kind="confirmation_section", expanded_codepoints=1),
            ),
            source_digest="a" * 64,
        )

    with pytest.raises(ReportPreviewError, match="変更"):
        create_preview(
            None,
            run_id="run-a",
            operation_key="op",
            plan=plan,
            contract=ReportOutputContract(),
            render_body=mutating,
        )
    assert isinstance(plan.blocks[0], ConfirmationSection) and plan.blocks[0].items == []


def test_source_hash_only_accepts_json_safe_snapshot() -> None:
    with pytest.raises(ReportPreviewError):
        report_source_digest({"unserializable": object()})


@pytest.mark.parametrize("version", [None, True, 1.0, 2])
def test_invalid_ledger_version_cannot_be_defaulted_on_restore(version: Any) -> None:
    created = _preview()
    ledger = deepcopy(created.ledger)
    ledger["schema_version"] = version
    with pytest.raises(ReportPreviewError) as error:
        select_preview(
            ledger,
            run_id="run-a",
            selection=FinalSelection(preview_id="A1"),
            contract=ReportOutputContract(),
        )
    assert error.value.code == "runtime.report_preview_invalid"


def test_trusted_current_source_digest_is_checked_without_rerendering() -> None:
    created = _preview()
    digest = created.ledger["records"][0]["source_digest"]
    selected = select_preview(
        created.ledger,
        run_id="run-a",
        selection=FinalSelection(preview_id="A1"),
        contract=ReportOutputContract(),
        source_digest=digest,
    )
    assert selected.status == "complete"
    with pytest.raises(ReportPreviewError) as error:
        select_preview(
            created.ledger,
            run_id="run-a",
            selection=FinalSelection(preview_id="A1"),
            contract=ReportOutputContract(),
            source_digest="b" * 64,
        )
    assert error.value.code == "report.source_changed"


def test_exact_hard_boundary_is_allowed_and_soft_target_remains_advisory() -> None:
    size = _preview().result.total_codepoints
    contract = ReportOutputContract(budget=AnswerBudget(soft_target=size - 1, hard_max=size))
    created = _preview(contract=contract)
    assert created.result.status == "OVER_TARGET" and created.result.remaining_hard == 0
    selected = select_preview(
        created.ledger, run_id="run-a", selection=FinalSelection(preview_id="A1"), contract=contract
    )
    assert selected.status == "complete" and len(selected.body or "") == size
    exact = _preview(
        contract=ReportOutputContract(budget=AnswerBudget(soft_target=size, hard_max=size))
    )
    assert exact.result.status == "WITHIN_BUDGET" and exact.result.remaining_target == 0


def test_growing_ledger_byte_limit_does_not_publish_a_partial_new_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _preview()
    original = deepcopy(created.ledger)
    old_size = len(json.dumps(original, ensure_ascii=False, separators=(",", ":")).encode())
    monkeypatch.setattr(report_preview, "MAX_REPORT_LEDGER_BYTES", old_size + 1)
    with pytest.raises(ReportPreviewError) as error:
        create_preview(
            created.ledger,
            run_id="run-a",
            operation_key="op-new",
            plan=_plan(),
            contract=ReportOutputContract(),
            render_body=_renderer(),
        )
    assert error.value.code == "runtime.report_preview_limit"
    assert created.ledger == original and len(created.ledger["records"]) == 1


@pytest.mark.parametrize("value", ["invalid", [], True, 5])
def test_non_object_checkpoint_is_reported_as_typed_corruption(value: Any) -> None:
    with pytest.raises(ReportPreviewError) as error:
        select_preview(
            value,
            run_id="run-a",
            selection=FinalSelection(preview_id="A1"),
            contract=ReportOutputContract(),
        )
    assert error.value.code == "runtime.report_preview_invalid"


def test_frozen_evidence_view_excludes_program_labels_and_is_hash_protected() -> None:
    def rendered(plan: AnswerPlan) -> RenderedReport:
        return RenderedReport(
            body="model evidence\nprogram literal chunk-opaque",
            evidence_text="model evidence",
            block_costs=(RenderedBlockCost(index=1, kind="narrative", expanded_codepoints=14),),
            composition_delta_chars=len("model evidence\nprogram literal chunk-opaque") - 14,
            source_digest="a" * 64,
        )

    created = create_preview(
        None,
        run_id="run-a",
        operation_key="op-a",
        plan=AnswerPlan(blocks=(NarrativeBlock(text="model evidence"),)),
        contract=ReportOutputContract(),
        render_body=rendered,
    )
    selected = select_preview(
        json.loads(json.dumps(created.ledger)),
        run_id="run-a",
        selection=FinalSelection(preview_id="A1"),
        contract=ReportOutputContract(),
    )
    assert selected.evidence_text == "model evidence"
    assert selected.body is not None and "chunk-opaque" in selected.body
    assert "evidence_text" not in created.result.model_dump()
    altered = deepcopy(created.ledger)
    altered["records"][0]["evidence_text"] = "program literal chunk-opaque"
    with pytest.raises(ReportPreviewError):
        select_preview(
            altered,
            run_id="run-a",
            selection=FinalSelection(preview_id="A1"),
            contract=ReportOutputContract(),
        )


def test_hard_max_does_not_publish_the_separate_evidence_view() -> None:
    contract = ReportOutputContract(budget=AnswerBudget(hard_max=1))

    def rendered(plan: AnswerPlan) -> RenderedReport:
        return RenderedReport(
            body="model text",
            evidence_text="model text",
            block_costs=(RenderedBlockCost(index=1, kind="narrative", expanded_codepoints=10),),
            source_digest="a" * 64,
        )

    created = create_preview(
        None,
        run_id="run-a",
        operation_key="op-a",
        plan=AnswerPlan(blocks=(NarrativeBlock(text="model text"),)),
        contract=contract,
        render_body=rendered,
    )
    selected = select_preview(
        created.ledger, run_id="run-a", selection=FinalSelection(preview_id="A1"), contract=contract
    )
    assert (
        selected.status == "incomplete" and selected.body is None and selected.evidence_text == ""
    )
