"""有効なMarkdown公開の明示確認だけで単位の信頼を固定する。"""

from __future__ import annotations

import copy

import pytest
from test_nl2sql_markdown_unification import Parser
from test_nl2sql_ontology_definitions import runtime
from test_nl2sql_ontology_workspace import model

from app.features.nl2sql.ontology_markdown_workspace import (
    MarkdownConfirmRequest,
    MarkdownOntologyWorkspace,
)
from app.features.nl2sql.ontology_unified_model import render_concepts
from app.features.nl2sql.result_units import confirmed_unit_records, published_unit_definitions
from app.settings import get_settings

pytestmark = pytest.mark.rule_parser


def test_active_prepare_confirm_load_and_restart_preserve_explicit_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "auth_mode", "local")
    monkeypatch.setattr(get_settings(), "nl2sql_ontology_worker_mode", "external")
    rt, _ = runtime()
    defs = model()
    defs[1].unit = "absolute-unit"
    defs[1].difference_unit = "difference-unit"
    markdown = render_concepts(defs)
    assert "差分の単位（Difference Unit）: difference-unit" in markdown
    base = rt.profile_view("sales")[1]
    rt.create_build_markdown_draft(
        profile_id="sales",
        base_revision_id=base.revision.id,
        payloads=[],
        titles=[],
        markdown=markdown,
        note="確認",
    )
    parser = Parser(defs)
    rt.legacy_service._enterprise_ai_client = parser
    svc = MarkdownOntologyWorkspace(rt)
    state = rt.ontology_markdown_state("sales")
    prepared = svc.prepare("sales", state.draft_etag, "unit-prepare", None)
    svc.run_preparation("sales", prepared["id"])
    ready = svc.preparation("sales", prepared["id"])
    assert ready["status"] == "ready", ready
    assert parser.calls == 0
    request = MarkdownConfirmRequest(
        preparation_id=ready["id"], draft_etag=ready["draft_etag"], confirmed=True
    )
    job = svc.publish("sales", request, "unit-publish", None)
    snapshot = svc.snapshot("sales", job.id)
    assert snapshot is not None
    assert len(snapshot["unit_review_records"]) == 1
    loaded = published_unit_definitions(rt, "sales", job.id)
    assert (
        len(loaded) == 1
        and loaded[0].unit == "absolute-unit"
        and loaded[0].difference_unit == "difference-unit"
    )
    assert published_unit_definitions(rt, "sales", job.id) == loaded
    # 別workspaceで同じ保存版を開き直す。新たな解析や現在の下書きは参照しない。
    reopened = MarkdownOntologyWorkspace(rt).snapshot("sales", job.id)
    assert reopened is not None
    assert reopened["unit_review_records"] == snapshot["unit_review_records"]
    original = rt.store.get_artifact(job.id)
    assert original is not None

    def current_etag() -> str:
        current = rt.store.get_artifact(job.id)
        assert current is not None
        return str(current["etag"])

    for change in ["missing_record", "not_confirmed", "tampered_definition"]:
        changed = copy.deepcopy(snapshot)
        if change == "missing_record":
            changed.pop("unit_review_records")
        elif change == "not_confirmed":
            changed["request"]["confirmed"] = False
        else:
            next(d for d in changed["definitions"] if d.get("unit"))["unit"] = "different"
        rt.store.save_artifact(
            svc.artifact("sales", job.id, "ontology_markdown_snapshot", changed),
            expected_etag=current_etag(),
        )
        assert published_unit_definitions(rt, "sales", job.id) == []
    rt.store.save_artifact(original, expected_etag=current_etag())
    corrupt = dict(original)
    corrupt["content"] = original["content"].replace("absolute-unit", "forged")
    rt.store.save_artifact(corrupt, expected_etag=current_etag())
    with pytest.raises(Exception, match="整合性"):
        published_unit_definitions(rt, "sales", job.id)


def test_unwritten_or_changed_units_cannot_be_confirmed_by_self_claimed_review() -> None:
    definitions = model()
    definitions[1].unit = "u"
    definitions[1].difference_unit = "du"
    source = render_concepts(definitions)
    assert len(confirmed_unit_records(source, definitions, "actor")) == 1
    source_without_unit = source.replace("- 単位（Unit）: u\n", "")
    definitions[1].review_status = "reviewed"
    assert confirmed_unit_records(source_without_unit, definitions, "actor") == []
    definitions[1].unit = "AI-changed"
    assert confirmed_unit_records(source, definitions, "actor") == []
    assert confirmed_unit_records(source + source, definitions, "actor") == []
