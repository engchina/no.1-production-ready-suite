"""条の目録で選んだ条を、生成で必ず検討させる（#3526）。

目録の段が選んだ条の child に重なる根拠に印（catalog）を付け、生成の計画（request_coverage）でその根拠を使う
（evidence_ids）か関係しない（unrelated）かを決めさせる。決めていない草稿は、計画の不整合と同じく 1 回だけ
作り直す。出典に使うかは原文が説明を支えるかで決め、引用は強いない。
"""

from __future__ import annotations

import json
from dataclasses import replace

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import grounded
from rag_engine.generation.answering import AnswerContext, AnswerRecord, _grounded_spans, synthesize_grounded_answer
from rag_engine.models.llm import GroundedDraft
from rag_engine.retrieval.article_catalog import ARTICLE_CATALOG_KEY, ARTICLE_UNIT_KEY
from rag_engine.retrieval.context_builder import ContextChildEvidence, ContextParentEvidence
from rag_engine.retrieval.evidence_selection import evidence_spans

QUESTION = "申請書の提出期限を教えてください"
ARTICLE_1 = "第一条\n申請書は、事由の発生した日から三十日以内に提出しなければならない。ただし、第三号に該当する者を除く。"
ARTICLE_2 = "第二条\n申請書には、事由を証する書類を添えなければならない。"
GUIDE = "提出の手順\n申請書は、事由の発生した日から三十日以内に窓口へ提出します。"
CLAIM = "申請書は、事由の発生した日から三十日以内に提出します。"


def _record(uid: str, text: str, level: str, source: str, **kwargs) -> AnswerRecord:
    return AnswerRecord(
        id=uid,
        chunk_uid=uid,
        chunk_id=uid,
        source=source,
        source_run_id="run",
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="Chunk",
        chunk_level=level,
        text=text,
        **kwargs,
    )


RULES = _record(
    "rule:p", ARTICLE_1 + "\n" + ARTICLE_2, "parent", "規則A.xml", child_chunk_ids=("rule:c1", "rule:c2"), metadata={ARTICLE_UNIT_KEY: True}
)
# 目録の段は選んだ条の章節の最初の child に印を付けて返す（`_catalog_article_children`）。
CHOSEN = _record(
    "rule:c1",
    ARTICLE_1,
    "child",
    "規則A.xml",
    parent_chunk_uid="rule:p",
    metadata={"section_path": ["総則", "第一条"], ARTICLE_CATALOG_KEY: {"title": "第一条", "order": 0}},
)
OTHER = _record("rule:c2", ARTICLE_2, "child", "規則A.xml", parent_chunk_uid="rule:p", metadata={"section_path": ["総則", "第二条"]})
MANUAL = _record("guide:p", GUIDE, "parent", "手順書B.pdf", metadata={"section_path": ["提出の手順"]})


def _context() -> AnswerContext:
    tree = (
        ContextParentEvidence(
            record=RULES,
            role="synthesis_parent",
            reason="catalog_article",
            children=(
                ContextChildEvidence(record=CHOSEN, role="retrieved_anchor", reason="catalog_article", retrieval_rank=2),
                ContextChildEvidence(record=OTHER, role="neighbor_context", reason="adjacent_child"),
            ),
        ),
        ContextParentEvidence(record=MANUAL, role="synthesis_parent", reason="r"),
    )
    return AnswerContext(records=[MANUAL, RULES], text=GUIDE + "\n" + RULES.text, evidence_tree=tree)


def _labels() -> dict[str, str]:
    spans = _grounded_spans(QUESTION, _context(), (), None)
    return {"article_1": _label(spans, "第一条"), "article_2": _label(spans, "第二条"), "guide": _label(spans, "提出の手順")}


def _label(spans, head: str) -> str:
    return next(span["evidence_id"] for span in spans if span["text"].startswith(head))


class _Model:
    def __init__(self, drafts: list[dict], audits: list[dict] = ()):
        self.drafts, self.audits, self.prompts = list(drafts), list(audits), []

    def __call__(self, system, prompt, settings, schema, **options):
        self.prompts.append((schema.__name__, prompt))
        source = self.drafts if schema is GroundedDraft else self.audits
        value = source.pop(0) if len(source) > 1 else source[0]
        return schema.model_validate(value(prompt) if callable(value) else value)


def _run(model, mode=grounded.AUDIT_MODE_NONE):
    def unused(*a, **k):
        raise AssertionError("unexpected I/O")

    settings = replace(get_settings(), answer_audit_mode=mode)
    with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
        return synthesize_grounded_answer(QUESTION, _context(), settings, image_prompt_mode="text_only").response


def _draft(items: list[dict], scope: list[tuple[str, str]], evidence_ids: list[str]) -> dict:
    plan = {
        "request_id": "Q1",
        "evidence_scope": [{"evidence_id": eid, "target": "申請書", "relation": relation} for eid, relation in scope],
        "evidence_ids": evidence_ids,
    }
    return {"request_coverage": [plan], "items": items, "summary": "", "confidence": "high"}


def _claim(*evidence_ids: str, text: str = CLAIM) -> dict:
    return {"kind": "rule", "request_id": "Q1", "text": text, "citations": [{"evidence_id": eid} for eid in evidence_ids]}


def _timing(result) -> dict:
    return result.generation_trace["rounds"][0]["timing"]


def test_only_the_evidence_over_the_chosen_article_is_marked():
    spans = evidence_spans(QUESTION, [RULES], scope_records=[CHOSEN, OTHER])
    marked = {span["text"].split("\n", 1)[0]: span[ARTICLE_CATALOG_KEY] for span in spans}
    assert marked == {"第一条": True, "第二条": False}


def test_the_prompt_shows_the_catalog_mark_on_the_evidence_line():
    spans = grounded.tag_spans(QUESTION, _grounded_spans(QUESTION, _context(), (), None))
    lines = [line for line in grounded.format_functions(spans).splitlines() if line.startswith("[E")]
    assert [line.endswith(" catalog") for line in lines].count(True) == 1
    assert "catalog" in grounded.GENERATE_SYSTEM_PROMPT
    assert "catalog" in GroundedDraft.model_json_schema()["$defs"]["RequestCoverage"]["properties"]["evidence_scope"]["description"]


def test_a_draft_that_does_not_consider_the_chosen_article_is_repaired_once():
    labels = _labels()
    first = _draft([_claim(labels["guide"])], [(labels["guide"], "direct")], [labels["guide"]])
    repaired = _draft(
        [_claim(labels["guide"], labels["article_1"])],
        [(labels["guide"], "direct"), (labels["article_1"], "direct")],
        [labels["guide"], labels["article_1"]],
    )
    model = _Model([first, repaired], [_audit("supported", "matched", "")])

    result = _run(model)

    drafts = [prompt for name, prompt in model.prompts if name == "GroundedDraft"]
    assert len(drafts) == 2
    repair = json.loads(drafts[1][drafts[1].rindex('{"invalid_request_plan"') :])
    assert [error.split(" ", 1)[0] for error in repair["invalid_request_plan"]] == [_catalog_label(drafts[0])]
    timing = _timing(result)
    assert (timing["catalog_undecided"], timing["catalog_undecided_after_repair"], timing["generation_calls"]) == (1, 0, 2)
    assert {entry["source"] for entry in result.cited_evidence} == {"規則A.xml", "手順書B.pdf"}


def _catalog_label(prompt: str) -> str:
    """prompt で catalog の印を付けた根拠の表示 ID（`[E1]`）。"""
    return next(line[1 : line.index("]")] for line in prompt.splitlines() if line.startswith("[E") and line.endswith(" catalog"))


def test_a_chosen_article_judged_unrelated_needs_no_repair():
    labels = _labels()
    model = _Model(
        [_draft([_claim(labels["guide"])], [(labels["guide"], "direct"), (labels["article_1"], "unrelated")], [labels["guide"]])]
    )

    result = _run(model)

    assert [name for name, _prompt in model.prompts] == ["GroundedDraft"]
    assert "catalog_undecided" not in _timing(result)


def test_a_planned_article_is_not_forced_into_a_citation():
    """計画に入れた条を説明が引かなくても作り直さない。出典は原文が説明を支える説明だけに付く。"""
    labels = _labels()
    model = _Model(
        [
            _draft(
                [_claim(labels["guide"])],
                [(labels["guide"], "direct"), (labels["article_1"], "direct")],
                [labels["guide"], labels["article_1"]],
            )
        ]
    )

    result = _run(model)

    assert [name for name, _prompt in model.prompts] == ["GroundedDraft"]
    assert {entry["source"] for entry in result.cited_evidence} == {"手順書B.pdf"}


def _audit(support: str, applicability: str, condition: str):
    """監査に渡した説明ごとに同じ判定を返す（条の出典の説明の監査）。"""

    def respond(prompt: str) -> dict:
        items = json.loads(prompt)["items"]
        return {
            "goal_alignment": "aligned",
            "summary_supported": True,
            "reviews": [
                {"index": entry["index"], "support": support, "applicability": applicability, "condition": condition, "reason": "r"}
                for entry in items
            ],
            "request_reviews": [{"request_id": "Q1", "status": "addressed", "reason": "r"}],
        }

    return respond


def test_a_claim_omitting_the_articles_exception_is_conditional_and_keeps_its_citation_line():
    """本則を述べ例外を省いた説明は、監査が conditional と判定すれば条件を添えて出典行を残す（unsupported にしない）。"""
    labels = _labels()
    draft = _draft([_claim(labels["article_1"])], [(labels["article_1"], "direct")], [labels["article_1"]])
    model = _Model([draft], [_audit("supported", "conditional", "第三号に該当する者を除く")])

    result = _run(model)

    assert [name for name, _prompt in model.prompts] == ["GroundedDraft", "GroundedFactAudit"]
    assert not result.envelope["passage_marks"]
    body = result.answer_text
    assert grounded.answer_labels().condition(grounded._condition_phrase("第三号に該当する者を除く")) + CLAIM in body
    prefix = grounded.answer_labels().citation_prefix
    assert [line for line in body.splitlines() if line.startswith(prefix) and "規則A.xml" in line]
    assert "例外" in grounded.audit_system_prompt(detailed=False)
