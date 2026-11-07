"""独立の審査が valid の根拠にした原文を、説明の段にも渡す（#4589）。外部I/Oなし。

合成の資料は 1 文書の 3 単位（加算の方法の文・判定表・区分の規則）。推導は判定表の行だけを引用し、結論は
合計点と区分。独立の審査は送った原文の全体を見て valid にし、判定の根拠に 3 単位の evidence_id を返す
（実モデルの記録と同じ形）。説明の段の入力が推導の引用だけだと、説明の段は区分の規則を見られない。

説明の段の応答は、記録の再生で返す: 入力に区分の規則の原文が無ければ、記録された「資料に区分の基準が示されて
いない」の説明を、有れば規則を使った説明を返す（モデルの振る舞いの写し。意味の判定はテストの中だけ）。
"""

import json

import pytest
from application_verdict_stub import audit_reply

from rag_engine.config import get_settings
from rag_engine.generation import application_explanation as explanation
from rag_engine.generation import grounded
from rag_engine.generation.application_reason import AuditReason
from rag_engine.generation.application_review import review_applications
from rag_engine.generation.applications import CheckedApplication, render_lines
from rag_engine.generation.citation_unit_ids import unit_generation_model
from rag_engine.generation.citation_wire import canonical_generation_draft, generation_draft_wire_model
from rag_engine.generation.row_conditions import RowConditionsResult, TableOutcome
from rag_engine.models.llm import ApplicationVerdictAudit, GroundedApplication

QUESTION = "属性P〜Rは有です。属性Sは無です。確定した合計点と区分を教えてください。"
METHOD = "合成資料\n判定表。各行の条件に一致するときだけ点数を加算し、一致しない行は0点とします。"
TABLE = "| 行ID | 条件 | 点数 |\n| R1 | 属性P=有 | 4 |\n| R2 | 属性Q=有 | 5 |\n| R3 | 属性R=有 | 3 |\n| R4 | 属性S=有 | 6 |"
ROWS = "| R1 | 属性P=有 | 4 |\n| R2 | 属性Q=有 | 5 |\n| R3 | 属性R=有 | 3 |\n| R4 | 属性S=有 | 6 |"
RULE = "区分の規則\n合計点の区分は、0〜9点は区分X、10〜19点は区分Y、20〜29点は区分Zです。"
RESULT = "合計点は 12 点、区分は Y です。"
# 記録の説明（区分の規則を見られなかった版と、見た版）。
RECORDED_WITHOUT_RULE = (
    "表の行の判定から、成り立つ行の点数を足すと 4 + 5 + 3 = 12 点です。資料に区分の基準が示されていないため、区分は特定できません。"
)
RECORDED_WITH_RULE = (
    "表の行の判定から、成り立つ行の点数を足すと 4 + 5 + 3 = 12 点です。区分の規則では10〜19点が区分Yなので、区分は Y です。"
)
IDS = ("evidence-method", "evidence-table", "evidence-rule")


def spans():
    rows = []
    for order, (evidence_id, text) in enumerate(zip(IDS, (METHOD, TABLE, RULE), strict=True)):
        rows.append(
            {
                "evidence_id": evidence_id,
                "label": f"E{order + 1}",
                "source_id": f"doc-1:cs-1:chunk-{order}",
                "source": "synthetic.md",
                "document_scope": ["synthetic.md", "cs-1", "doc-1"],
                "page": 1,
                "text": text,
                "start": 0,
                "end": len(text),
                "function": ("synthetic.md", "", "synthetic-sha", "unstructured", "cs-1", "doc-1", ""),
                "section_path": [],
                "source_order": [order, order, 0],
            }
        )
    return rows


def table_entry(sent):
    """判定表の単位だけを引用した要点（検証済みの引用の束縛を持つ）。"""
    grounded.format_functions(sent)
    catalog = grounded.generation_unit_catalog(sent)
    model = unit_generation_model(generation_draft_wire_model(), catalog)
    raw = model.model_validate(
        {
            "items": [
                {
                    "request_id": "Q1",
                    "kind": "rule",
                    "text": ROWS,
                    "citations": [{"unit_ids": list(catalog.snapshots[1].unit_ids[1:])}],
                }
            ],
            "request_coverage": [
                {"request_id": "Q1", "evidence_scope": [{"evidence_id": "E2", "target": "合計点", "relation": "direct", "link": None}]}
            ],
            "summary": "",
            "confidence": "low",
        }
    )
    entries, dropped = grounded.verify(QUESTION, canonical_generation_draft(raw, unit_catalog=catalog), sent)
    assert len(entries) == 1 and not dropped
    return entries[0]


def row_conditions():
    """判定表の行の判定（行の段の結果。決まった表）。"""
    rows = [
        ("R1", "属性P=有", "applies"),
        ("R2", "属性Q=有", "applies"),
        ("R3", "属性R=有", "applies"),
        ("R4", "属性S=有", "does_not_apply"),
    ]
    application = {
        "rows": [
            {
                "row_raw": f"| {row} | {condition} |",
                "condition_cell": {"raw": condition},
                "applicability": result,
                "decided_by": "synthetic",
            }
            for row, condition, result in rows
        ]
    }
    table = TableOutcome(evidence_id="evidence-table", source="synthetic.md", table_result="determined", application=application)
    return RowConditionsResult(status="completed", tables=[table])


def run(*, valid=True, basis=IDS):
    """審査（判定と理由）→ 説明の段を通し、説明の段の入力を返す。"""
    sent = spans()
    application = CheckedApplication(
        GroundedApplication(item_indexes=[0], reasoning="表の点数を合計し、区分の規則に当てはめる。", result=RESULT), [table_entry(sent)]
    )
    settings = get_settings(environ={}, dotenv_path=None)

    def audit(system, prompt, settings, schema, **options):
        return audit_reply(schema, [{"index": 0, "valid": valid, "basis_evidence_ids": list(basis)}], prompt=prompt)

    review = review_applications(
        QUESTION,
        [application],
        evidence_spans=sent,
        existing_audit=None,
        audit_requested=False,
        parse_text=audit,
        settings=settings,
        provider_id=None,
        row_conditions=row_conditions(),
    )
    prompts = []

    def replay(system, prompt, settings, schema, **options):
        assert schema is not AuditReason and schema is not ApplicationVerdictAudit
        prompts.append(json.loads(prompt))
        sources = "\n".join(prompts[-1][explanation.KEY_SOURCES])
        return schema.model_validate({"explanation": RECORDED_WITH_RULE if RULE in sources else RECORDED_WITHOUT_RULE})

    trace = explanation.explain_applications(
        QUESTION, [application], None, parse_text=replay, settings=settings, provider_id=None, row_conditions=row_conditions()
    )
    return application, review, trace, prompts


def test_explanation_sees_the_units_the_audit_accepted_the_conclusion_on():
    application, review, trace, prompts = run()
    assert application.published and application.assessment["status"] == "valid"
    assert trace["calls"] == 1 and len(prompts) == 1
    sources = prompts[0][explanation.KEY_SOURCES]
    # 推導の引用（表の行）を先に、審査が根拠にした送った原文（推導が引用していないもの）をその後に渡す。
    assert sources == [ROWS, METHOD, RULE]
    # 表の行の判定（#4576）は今までどおり渡す。
    assert [row[explanation.KEY_ROW_RESULT] for row in prompts[0][explanation.KEY_ROWS]] == ["成り立つ"] * 3 + ["成り立たない"]
    assert review["explanation_basis"] == {"0": ["evidence-method", "evidence-rule"]}
    rendered = "\n".join(render_lines([application], lambda _: "根拠：synthetic.md", lambda _: None))
    assert RECORDED_WITH_RULE in rendered and RECORDED_WITHOUT_RULE not in rendered


def test_basis_ids_that_were_not_sent_are_not_passed():
    _application, review, _trace, prompts = run(basis=("evidence-table", "evidence-unsent"))
    assert prompts[0][explanation.KEY_SOURCES] == [ROWS]
    assert "explanation_basis" not in review and review["unknown_basis_ids"] == {"0": ["evidence-unsent"]}


@pytest.mark.parametrize("basis", [("evidence-rule",), ()], ids=["uncited_only", "empty"])
def test_valid_without_the_cited_source_is_still_unknown_and_not_explained(basis):
    # 引用元を含まない根拠の valid は未確認のまま（basis_not_cited）。説明の段は呼ばない。
    application, review, trace, prompts = run(basis=basis)
    assert application.assessment["status"] == "unknown" and not application.published
    assert review["basis_not_cited"] == [0] and "explanation_basis" not in review
    assert trace["calls"] == 0 and prompts == [] and application.audit_basis == []


def test_invalid_verdict_does_not_extend_the_explanation_inputs():
    application, review, trace, prompts = run(valid=False)
    assert not application.published and application.audit_basis == []
    assert "explanation_basis" not in review and trace["calls"] == 0 and prompts == []


def test_table_rows_of_an_audit_basis_table_are_passed():
    """審査が根拠にした別の表（推導が引用していない）の行の判定も、説明の段へ渡す。"""
    entry = CheckedApplication(GroundedApplication(item_indexes=[0], reasoning="合成の推導。", result=RESULT), [])
    entry.audit_basis = [{"evidence_id": "evidence-table", "text": TABLE}]
    rows = explanation.application_rows(entry, row_conditions())
    assert [row[explanation.KEY_ROW_CONDITION] for row in rows] == ["属性P=有", "属性Q=有", "属性R=有", "属性S=有"]
    assert explanation.application_sources(entry) == [TABLE]
