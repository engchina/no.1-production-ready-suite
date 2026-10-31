"""処理の経過の「用語・同義語の確認」と「回答ルールの確認」（#3451）。

検索・回答プロファイルの知識の用語・同義語（別名を検索に使う）と回答ルール（回答の参考にする）を、別の工程
（別の trace の span。同じ親の子でこの順）に記録し、それぞれの登録の数・一致・影響を示すことを、合成の登録と
質問で確かめる（外部のサービスには接続しない）。
"""

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from grounded_stub import echo_model
from test_answer_generation import _chunk_run_stub, _hybrid_result, _rerank_ready_settings, _stored_child

from rag_engine.adapters.oci import RerankTextRank
from rag_engine.generation import answering
from rag_engine.generation.answering import SIMPLE_RETRIEVAL_LABEL, STANDARD_ANSWER_FLOW_LABEL, answer_question_result
from rag_engine.resources.tracing import bind_trace_observer

TERMS_STEP = answering.TERM_SEARCH_STEP
ANSWER_TERMS_STEP = answering.TERM_ANSWER_STEP
RULES_STEP = answering.RULE_ANSWER_STEP

_TERM = {"term": "合成用語A", "aliases": ["合成別名A"], "description": "合成の説明"}
_INACTIVE_TERM = {"term": "合成用語Z", "status": "deprecated"}
_RULE = {"id": "rule-a", "title": "合成回答ルールA", "triggers": ["合成の期限"], "content": "合成のルールの本文"}
_INACTIVE_RULE = {"id": "rule-z", "title": "合成回答ルールZ", "triggers": ["合成の下書き"], "content": "未承認", "status": "draft"}


class _SpanRecorder:
    """工程の観測（span）の開いた順と親（開いている span）を記録する。"""

    def __init__(self) -> None:
        self.spans: list[tuple[str, str | None]] = []
        self._stack: list[str] = []

    @contextmanager
    def observe(self, operation: str, model: str) -> Iterator["_SpanRecorder"]:
        name = f"{operation}:{model}"
        self.spans.append((name, self._stack[-1] if self._stack else None))
        self._stack.append(name)
        try:
            yield self
        finally:
            self._stack.pop()

    def usage(self, value: object) -> None:
        pass

    def display(self, kind: str | None = None, params: Mapping[str, object] | None = None, details: Any = None) -> None:
        pass


def _write(path: Path, *, terms: list[dict] | None = None, rules: list[dict] | None = None) -> Path:
    path.write_text(json.dumps({"schema_version": 1, "terms": terms or [], "rules": rules or []}, ensure_ascii=False), encoding="utf-8")
    return path


def _run(
    knowledge_path: Path,
    question: str = "合成用語Aの合成の期限は？",
    observer: _SpanRecorder | None = None,
    *,
    search: bool = False,
    answer: bool = False,
    strategy: str = SIMPLE_RETRIEVAL_LABEL,
):
    settings = replace(
        _rerank_ready_settings(),
        runtime_knowledge_path=knowledge_path,
        runtime_terms_search_enabled=search,
        runtime_terms_answer_enabled=answer,
        runtime_rules_answer_enabled=True,
    )
    search_result = _hybrid_result(_stored_child("chunk-docling-c000001", 1, "合成の本文"))
    with (
        patch("rag_engine.generation.answering.load_latest_or_source_chunk_run", return_value=_chunk_run_stub()),
        patch("rag_engine.generation.answering.check_adb_hybrid_search_ready"),
        patch("rag_engine.generation.answering.search_adb_hybrid_chunks", return_value=search_result),
        patch("rag_engine.generation.answering.rerank_text_with_scores", return_value=[RerankTextRank(0, 0.9)]),
        patch("rag_engine.generation.answering.parse_text_response", side_effect=echo_model),
    ):
        if observer is None:
            return answer_question_result(
                question,
                "abcdef",
                ["docling"],
                settings,
                query_strategy=strategy,
                answer_flow=STANDARD_ANSWER_FLOW_LABEL,
            )
        with bind_trace_observer(observer.observe):
            return answer_question_result(
                question,
                "abcdef",
                ["docling"],
                settings,
                query_strategy=strategy,
                answer_flow=STANDARD_ANSWER_FLOW_LABEL,
            )


def _steps(result) -> dict[str, dict]:
    return {step["name"]: step for step in result.execution_steps}


def _fields(step: dict) -> dict[str, str]:
    return {field["label"]: field["value"] for field in step.get("details", {}).get("fields", [])}


@pytest.mark.parametrize("search,answer", [(False, False), (True, False), (False, True), (True, True)])
def test_independent_steps_and_rules(tmp_path: Path, search: bool, answer: bool) -> None:
    """実行経路でも用途を分け、回答ルールはどちらの設定にも依存しない。"""
    recorder = _SpanRecorder()
    result = _run(_write(tmp_path / "rk.json", terms=[_TERM], rules=[_RULE]), observer=recorder, search=search, answer=answer)
    steps = _steps(result)
    for name, enabled in ((TERMS_STEP, search), (ANSWER_TERMS_STEP, answer)):
        assert steps[name]["status"] == ("complete" if enabled else "skipped")
        fields = _fields(steps[name])
        if enabled:
            assert fields["用語"] == _TERM["term"]
            assert fields["別名"] == _TERM["aliases"][0]
            assert ("説明" in fields) is (name == ANSWER_TERMS_STEP)
        else:
            assert "設定がオフ" in fields["結果"]
            assert "用語" not in fields
    assert any(_RULE["title"] in line for line in steps[RULES_STEP]["details"]["lines"])
    assert "用語・同義語の確認" not in steps
    for name in (TERMS_STEP, ANSWER_TERMS_STEP):
        assert any(span == f"stage:{name}" for span, _ in recorder.spans)
    if search:
        assert any("合成別名A" in line for line in steps[TERMS_STEP]["details"].get("lines", []))


@pytest.mark.parametrize("terms", [[], [_INACTIVE_TERM], [_TERM]])
def test_enabled_without_match_explains_no_usage(tmp_path: Path, terms: list[dict]) -> None:
    steps = _steps(_run(_write(tmp_path / "rk.json", terms=terms), question="合成の別の質問は？", search=True, answer=True))
    for name in (TERMS_STEP, ANSWER_TERMS_STEP):
        assert steps[name]["status"] == "complete"
        assert "一致しなかった" in _fields(steps[name])["結果"]
        assert "用語" not in _fields(steps[name])


def test_load_error_is_reported_once(tmp_path: Path) -> None:
    path = tmp_path / "rk.json"
    path.write_text("{broken", encoding="utf-8")
    reported: list[str] = []
    with patch.object(answering, "report_degraded", side_effect=lambda kind, exc: reported.append(kind)):
        _run(path)
    assert reported.count("runtime_knowledge") == 1


@pytest.mark.parametrize("scope", [("合成の条件 A", ())], ids=["scope"])
def test_chosen_conditions_are_shown_in_the_rules_step(tmp_path: Path, scope: tuple[str, tuple[str, ...]]) -> None:
    """確認で選んだ条件（回答ルールの確認の答え）は、回答ルールの工程に出す。"""
    settings = replace(_rerank_ready_settings(), runtime_knowledge_path=_write(tmp_path / "rk.json", rules=[_RULE]))
    with (
        patch("rag_engine.generation.answering.load_latest_or_source_chunk_run", return_value=_chunk_run_stub()),
        patch("rag_engine.generation.answering.check_adb_hybrid_search_ready"),
        patch(
            "rag_engine.generation.answering.search_adb_hybrid_chunks",
            return_value=_hybrid_result(_stored_child("chunk-docling-c000001", 1, "合成の本文")),
        ),
        patch("rag_engine.generation.answering.rerank_text_with_scores", return_value=[RerankTextRank(0, 0.9)]),
        patch("rag_engine.generation.answering.parse_text_response", side_effect=echo_model),
    ):
        result = answer_question_result(
            "合成の期限は？",
            "abcdef",
            ["docling"],
            settings,
            query_strategy=SIMPLE_RETRIEVAL_LABEL,
            answer_flow=STANDARD_ANSWER_FLOW_LABEL,
            scope=scope,
        )
    steps = _steps(result)
    assert result.runtime_knowledge.scope_context == "合成の条件 A"
    assert not any("確認で選んだ条件" in line for line in steps[TERMS_STEP].get("details", {}).get("lines", []))


def test_confirmation_only_rules_do_not_inject_generation_instructions(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "rk.json",
        rules=[_RULE | {"purpose": "confirmation", "content": "", "clarification": {"question": "対象は？", "options": []}}],
    )
    result = _run(path)
    assert result.runtime_knowledge.rules_for_stage("generation") == ()
    assert _steps(result)[answering.RULE_CONFIRMATION_STEP]["status"] == "skipped"


def test_term_matching_and_rewrite_overlap_and_remain_original_only(tmp_path: Path, monkeypatch) -> None:
    from dataclasses import replace
    from threading import Barrier

    from rag_engine.knowledge import runtime_knowledge as rk

    barrier = Barrier(2, timeout=3)
    original_match = rk._matched_terms
    seen = []

    def match(question, terms, limit):
        seen.append(question)
        barrier.wait()
        return original_match(question, terms, limit)

    def expand(question, strategy, settings, **kwargs):
        barrier.wait()
        return replace(answering._base_query_expansion(question, strategy), generated_queries=("合成用語Z の設定",))

    monkeypatch.setattr(rk, "_matched_terms", match)
    monkeypatch.setattr(answering, "build_query_expansion", expand)
    path = _write(tmp_path / "rk.json", terms=[_TERM, {"term": "合成用語Z", "aliases": ["再帰追加しない別名"]}])
    result = _run(path, search=True)
    assert seen == ["合成用語Aの合成の期限は?"]
    assert [term.term for term in result.runtime_knowledge.matched_terms] == [_TERM["term"]]
    assert "再帰追加しない別名" not in result.runtime_knowledge.expanded_question
    steps = _steps(result)
    assert steps[TERMS_STEP]["depth"] == steps["質問の言い換え・分解"]["depth"]
    names = list(steps)
    assert names.index("質問の言い換え・分解") < names.index(TERMS_STEP) < names.index("検索文をまとめ、検索語を作る")


def test_term_failure_does_not_stop_independent_rewrite(tmp_path: Path, monkeypatch) -> None:
    from dataclasses import replace

    from rag_engine.knowledge import runtime_knowledge as rk

    calls = []

    def fail(*args):
        raise ValueError("synthetic")

    def expand(question, strategy, settings, **kwargs):
        calls.append(question)
        return replace(answering._base_query_expansion(question, strategy), generated_queries=("合成の期限の説明",))

    monkeypatch.setattr(rk, "_matched_terms", fail)
    monkeypatch.setattr(answering, "build_query_expansion", expand)
    path = _write(tmp_path / "rk.json", terms=[_TERM])
    result = _run(path, search=True)
    assert calls and result.answer
    assert _steps(result)[TERMS_STEP]["status"] == "補助情報なしで続行"
    assert not result.runtime_knowledge.retrieval_terms


@pytest.mark.parametrize("search,answer", [(False, False), (True, False), (False, True), (True, True)])
def test_model_receives_only_enabled_terms_and_independent_rules(tmp_path: Path, search: bool, answer: bool) -> None:
    path = _write(tmp_path / "rk.json", terms=[_TERM], rules=[_RULE])
    prompts = []
    original_model = echo_model

    def model(*args, **kwargs):
        prompts.append(" ".join(str(arg) for arg in args[:2]))
        return original_model(*args, **kwargs)

    with patch.dict(_run.__globals__, {"echo_model": model}):
        result = _run(path, search=search, answer=answer)
    joined = "\n".join(prompts)
    assert ("合成の説明" in joined) is answer
    assert "合成のルールの本文" in joined
    assert result.runtime_knowledge.original_question in joined


def test_rewrite_failure_does_not_stop_term_search(tmp_path: Path) -> None:
    path = _write(tmp_path / "rk.json", terms=[_TERM])
    with patch.object(answering, "_route_query_strategy", side_effect=ValueError("synthetic")):
        result = _run(path, search=True, strategy="auto_routing")
    assert result.answer
    assert result.runtime_knowledge.retrieval_terms
    assert _steps(result)["質問の言い換え・分解"]["status"] == "補助情報なしで続行"
    assert _steps(result)[TERMS_STEP]["status"] == "complete"
