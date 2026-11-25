"""拡張の読取と文脈への採用を、合成の原文で別々に検証する。"""

from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any

import pytest
from test_section_expansion import SectionStub, _seed_context

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import answering
from rag_engine.generation.answer_models import AnswerRecord
from rag_engine.resources.context_expansion_trace import context_read_receipt
from rag_engine.resources.tracing import bind_trace_observer


@dataclass
class Observation:
    name: str
    inputs: Any = None
    outputs: Any = None
    params: dict = field(default_factory=dict)
    details: dict = field(default_factory=dict)

    def request(self, value, **kwargs):
        self.inputs = value

    def response(self, value):
        self.outputs = value

    def usage(self, value):
        pass

    def display(self, kind=None, params=None, details=None):
        self.params.update(params or {})
        self.details.update(details or {})


@contextmanager
def recorded():
    observations = []

    @contextmanager
    def observer(operation, name):
        entry = Observation(name)
        observations.append(entry)
        yield entry

    with bind_trace_observer(observer):
        yield observations


def unused(*args, **kwargs):
    raise AssertionError("unexpected I/O")


def fixed(uid, seq):
    return AnswerRecord(
        id=uid,
        chunk_uid=uid,
        text=f"合成本文 {uid}",
        chunk_level="child",
        chunk_seq=seq,
        source="synthetic.txt",
        engine="synthetic",
        engine_label="合成",
        page=1,
        seq_no=seq,
        category="text",
        metadata={"chunk_strategy": "fixed", "document_id": "doc-1", "chunk_set_id": "set-1"},
    )


@pytest.mark.parametrize("count,expected", [(1, 2), (5, 2)])
def test_neighbors_separate_read_and_adoption(count, expected):
    rows = [fixed("c1", 1), fixed("c2", 2), fixed("c3", 3)]

    def load(**kwargs):
        receipt = context_read_receipt()
        receipt.reads.append(
            {
                "state": "read",
                "read_ids": ["c1", "c1", "c2", "c3"],
                "scope_excluded_ids": [],
                "relations": [
                    {"chunk_id": "c1", "distance": -1, "purpose": "neighbor_context"},
                    {"chunk_id": "c3", "distance": 1, "purpose": "neighbor_context"},
                ],
            }
        )
        return []

    # 保存済みの候補を使い、I/O の観測だけを注入する。
    with bind_dependencies(AnswerDependencies(unused, unused, unused, unused, unused, context_chunks=load)), recorded() as calls:
        answering._bundle_context(
            "合成の質問",
            [rows[1]],
            rows,
            get_settings(environ={}, dotenv_path=None),
            top_k=1,
            neighbor_child_count=count,
            max_chars=10000,
        )
    step = next(call for call in calls if call.name == "文脈の補完")
    assert step.inputs["前後それぞれの最大 Chunk 数"] == count
    assert step.inputs["起点ごとの補い方"] == {"c2": "neighbors"}
    assert step.outputs["読取 Chunk 数"] == 3
    assert step.outputs["文脈への採用 Chunk 数"] == expected
    assert step.outputs["実行状態"] == "実行済み"


def run_sections(ctx, stub, settings=None, state=None):
    settings = settings or replace(get_settings(environ={}, dotenv_path=None), section_expansion_max_levels=1)
    with (
        bind_dependencies(
            AnswerDependencies(unused, unused, unused, unused, unused, section_outline=stub.outline, section_chunks=stub.chunks)
        ),
        recorded() as calls,
    ):
        result = answering._expand_context_by_sections("合成の質問", ctx, settings, started_at=0, state=state)
    return result, next(call for call in calls if call.name == "章節の拡張")


def test_sections_read_duplicates_and_sources_are_explicit():
    (ctx, _), step = run_sections(_seed_context("c31a"), SectionStub())
    output = step.outputs
    assert output["新規読取 Chunk 数"] == 2
    assert output["重複除去後の新規採用 Chunk 数"] == 1
    assert output["新規採用 ID"] == ["c31b"]
    assert output["読み取り候補のうち既存文脈と重複する ID"] == ["c31a"]
    relation = output["起点と章節・内容の対応"][0]
    assert relation["seed_ids"] == ["c31a"]
    assert relation["section_id"] == "s3-1"
    assert relation["newly_adopted_child_ids"] == ["c31b"]
    assert step.inputs["準備した章節範囲"][0]["sections"]
    assert len(ctx.evidence_tree) == 2


def test_sections_cross_path_duplicate_is_not_new():
    ctx = _seed_context("c31a", "c31b")
    tree = list(ctx.evidence_tree)
    tree[1] = replace(tree[1], children=(replace(tree[1].children[0], role="neighbor_context"),))
    ctx = replace(ctx, evidence_tree=tuple(tree))
    (_, _), step = run_sections(ctx, SectionStub())
    assert step.outputs["重複除去後の新規採用 Chunk 数"] == 0
    assert step.outputs["前後文脈と重複する ID"] == ["c31b"]
    assert "実行状態" not in step.outputs  # 実行して 0 件はスキップにしない。


def test_sections_no_mapping_is_completed_zero():
    (_, result), step = run_sections(_seed_context("c31a"), SectionStub(mapped=False))
    assert result.stop_reason == "no_sections"
    assert step.outputs["読取章節数"] == step.outputs["重複除去後の新規採用 Chunk 数"] == 0


def test_sections_disabled_and_same_context_reuse_are_distinct():
    settings = replace(get_settings(environ={}, dotenv_path=None), section_expansion_enabled=False)
    (_, _), step = run_sections(_seed_context("c31a"), SectionStub(), settings)
    assert step.outputs["実行状態"] == "設定が無効です。"
    state = {}
    stub = SectionStub()
    (ctx, _), _ = run_sections(_seed_context("c31a"), stub, state=state)
    reads = list(stub.read_calls)
    (_, _), step = run_sections(ctx, stub, state=state)
    assert step.outputs["実行状態"] == "読み取り済みの同じ文脈を再利用しました。"
    assert stub.read_calls == reads


def test_neighbor_no_available_content_is_completed_zero():
    with recorded() as calls:
        answering._bundle_context(
            "合成の質問",
            [fixed("c1", 1)],
            [fixed("c1", 1)],
            get_settings(environ={}, dotenv_path=None),
            top_k=1,
            neighbor_child_count=3,
            max_chars=10000,
        )
    output = calls[0].outputs
    assert output["文脈への採用 Chunk 数"] == 0
    assert output["実行状態"] == "実行済み"
    assert output["隣接候補の選択記録"][0]["reason"] == "no_available_neighbors"
    assert output["読み取り詳細"] == "未記録"


def test_section_budget_exclusion_keeps_actual_reason():
    settings = replace(
        get_settings(environ={}, dotenv_path=None),
        section_expansion_max_levels=1,
        answer_max_input_tokens=1,
    )
    (_, result), step = run_sections(_seed_context("c31a"), SectionStub(), settings)
    # 所有の章節も入力上限の 1/2 を超える: 読まず、回答に出さず、止めた理由に残す（#5812）。
    assert result.stop_reason == "token_budget"
    assert step.outputs["停止理由"] == "章節が入力上限の 1/2 を超えた、または根拠の全体が 1 回の生成の原文の予算を超えた（親・前後で補う）"
    assert not result.unread


def test_sections_that_push_the_evidence_into_batched_reading_are_not_read():
    """足すと根拠の全体が 1 回の生成の原文の予算を超える章節は読まず、回答に出さない（#5820）。"""
    ctx = _seed_context("c31a", "c2a")
    base = get_settings(environ={}, dotenv_path=None)
    # 分割読取に回すかを決める字数（根拠ブロックの字数）と同じ数え方で、当たった根拠だけが入る予算にする。
    block = answering._evidence_block_chars("合成の質問", ctx, base)
    settings = replace(base, section_expansion_max_levels=1, answer_max_input_tokens=2 * (block + 1))
    assert answering._evidence_block_chars("合成の質問", ctx, settings) == block <= settings.evidence_budget_chars

    (after, result), step = run_sections(ctx, SectionStub(), settings)

    assert [entry["read"] for entry in step.outputs["読取結果"][0]["sections"]] == ["over_total", "over_total"]
    assert step.inputs["根拠の全体の上限（文字。1 回の生成の原文の予算）"] == block + 1
    assert {"label": "根拠の全体の上限で読まなかった章節", "value": "2 件（起点は親・前後で補います）"} in step.details["fields"]
    assert result.stop_reason == "token_budget" and not result.unread and not result.applied
    assert after is ctx
    # 入る予算なら、これまでどおり章節の全体を足す。
    roomy = replace(base, section_expansion_max_levels=1)
    (_, kept), _ = run_sections(_seed_context("c31a", "c2a"), SectionStub(), roomy)
    assert kept.applied and kept.stop_reason == "scope_read"
