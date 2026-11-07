"""独立の監査の判定の根拠が、結論の引用元を含むかの ID 整合の門（basis_not_cited）。外部I/Oなし。

合成の 2 資料は同じ原文を持ち、結論は資料 1（evidence-1）を引用する。判定の根拠が資料 2 だけの valid は、
同じ文を持つ別資料への乗り換えの形なので採用しない。ID の集合の照合だけで、意味は判定しない。
"""

import copy

import pytest
from application_verdict_stub import audit_reply
from test_application_source_bindings import QUESTION, verified

from rag_engine.config import get_settings
from rag_engine.generation.application_reason import AuditReason
from rag_engine.generation.application_review import BASIS_NOT_CITED_REASON, review_applications
from rag_engine.generation.applications import CheckedApplication
from rag_engine.models.llm import ApplicationVerdictAudit, GroundedApplication


def run(entry, spans, valid, basis):
    application = CheckedApplication(
        GroundedApplication(item_indexes=[0], reasoning="選択原文の値を示す。", result="測定値は28です。"), [entry]
    )
    schemas = []

    def parse(system, prompt, settings, schema, **options):
        schemas.append(schema)
        return audit_reply(schema, [{"index": 0, "valid": valid, "basis_evidence_ids": basis}])

    trace = review_applications(
        QUESTION,
        [application],
        evidence_spans=spans,
        existing_audit=None,
        audit_requested=False,
        parse_text=parse,
        settings=get_settings(environ={}, dotenv_path=None),
        provider_id=None,
    )
    return application, trace, schemas


def test_valid_whose_basis_is_only_an_uncited_source_is_unknown_and_not_explained():
    entry, spans, _catalog = verified((0,))
    application, trace, schemas = run(entry, spans, True, ["evidence-2"])
    assert application.assessment == {"status": "unknown", "reason": BASIS_NOT_CITED_REASON}
    assert not application.published
    assert trace["basis_not_cited"] == [0] and trace["status"] == "incomplete"
    # 理由の段は送らない（判定 1 回だけ）。
    assert schemas == [ApplicationVerdictAudit] and trace["calls"] == 1


def test_valid_with_empty_basis_is_unknown_when_the_citation_is_bound():
    entry, spans, _catalog = verified((0,))
    application, trace, schemas = run(entry, spans, True, [])
    assert application.assessment["status"] == "unknown" and trace["basis_not_cited"] == [0]
    assert AuditReason not in schemas


@pytest.mark.parametrize("basis", [["evidence-1"], ["evidence-1", "evidence-2"], ["evidence-2", "evidence-1"]])
def test_valid_whose_basis_includes_the_cited_source_is_kept(basis):
    entry, spans, _catalog = verified((0,))
    application, trace, schemas = run(entry, spans, True, basis)
    assert application.assessment["status"] == "valid" and application.published
    assert "basis_not_cited" not in trace and schemas == [ApplicationVerdictAudit, AuditReason]


@pytest.mark.parametrize("basis", [["evidence-2"], [], ["evidence-1"]])
def test_invalid_is_never_changed_by_the_gate(basis):
    entry, spans, _catalog = verified((0,))
    application, trace, schemas = run(entry, spans, False, basis)
    assert application.assessment["status"] == "invalid" and not application.published
    assert "basis_not_cited" not in trace and AuditReason in schemas


def test_also_citation_counts_any_cited_source():
    entry, spans, _catalog = verified((0, 1))
    application, trace, _schemas = run(entry, spans, True, ["evidence-2"])
    assert application.assessment["status"] == "valid" and "basis_not_cited" not in trace


def test_application_without_verified_bindings_is_not_checked():
    entry, spans, _catalog = verified((0,))
    unbound = copy.deepcopy(entry)
    unbound.item.citations[0]._extraction["source_id"] = "foreign-source"
    application, trace, _schemas = run(unbound, spans, True, [])
    # 引用元の ID を照合できない推導は、この門の対象にしない（既存の判定のまま）。
    assert application.assessment["status"] == "valid" and "basis_not_cited" not in trace
