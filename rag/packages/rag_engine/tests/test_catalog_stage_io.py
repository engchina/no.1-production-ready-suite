"""目録の選択・再利用・実読取と返却候補を合成データで確認する。"""

import json
from contextlib import contextmanager, nullcontext
from dataclasses import replace

import pytest
from test_article_catalog import _stored
from test_query_stage_io import Recorder
from test_screen_catalog import _stored as screen_row

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import answering as a
from rag_engine.generation.execution_record import _execution_step, bind_step_listener
from rag_engine.resources.tracing import bind_trace_observer
from rag_engine.retrieval.article_catalog import ArticleCatalog, ArticleIssue, ArticleIssueSelection
from rag_engine.retrieval.screen_catalog import LinkedScreen, ScreenLinks

CATALOG = ArticleCatalog("doc-a", "synthetic.txt", "set-a", ("合成目録", "第一条（合成A）", "第二条（合成B）"), ("root", "s1", "s2"))


class PolicyRecorder(Recorder):
    def __init__(self, enabled=True):
        super().__init__()
        self.enabled = enabled

    @contextmanager
    def observe(self, operation, model):
        with super().observe(operation, model) as observation:
            observation.capture_enabled = self.enabled
            yield observation


def settings(**values):
    return replace(
        get_settings(environ={}, dotenv_path=None), article_catalog_refine_articles=0, article_catalog_scope_documents=0, **values
    )


def unused(*args, **kwargs):
    raise AssertionError("unexpected I/O")


@pytest.fixture(autouse=True)
def clear_caches():
    for cache in [a._SCREEN_CATALOG_CACHE, a._SCREEN_LINK_CACHE, a._ARTICLE_SELECTION_CACHE, a._ARTICLE_DOCUMENT_CACHE]:
        cache.clear()
    yield
    for cache in [a._SCREEN_CATALOG_CACHE, a._SCREEN_LINK_CACHE, a._ARTICLE_SELECTION_CACHE, a._ARTICLE_DOCUMENT_CACHE]:
        cache.clear()


@pytest.mark.parametrize("enabled", [True, False])
def test_screen_selection_records_actual_selection_read_and_cache(monkeypatch, enabled):
    screen = ("synthetic.txt", "（1）合成画面")
    loaded = [
        screen_row("old", screen[0], [screen[1]]),
        screen_row("new", screen[0], [screen[1]]),
        screen_row("parent", screen[0], [screen[1]], level="parent"),
    ]
    ranked = [a._stored_chunk_answer_record(loaded[0])]
    model_calls = []

    def parse(*args, **kwargs):
        model_calls.append(args)
        return ScreenLinks(candidates=[LinkedScreen(document=screen[0], screen=screen[1])])

    def chunks(*, links, existing, settings):
        assert links == [screen] and existing == {"old"}
        return loaded

    if not enabled:
        monkeypatch.setattr(a, "candidate_snapshot", unused)
    deps = AnswerDependencies(
        search=unused,
        check_ready=unused,
        rerank=unused,
        parse_images=unused,
        parse_text=parse,
        screen_catalog=lambda **kwargs: {screen[0]: [screen[1]]},
        screen_chunks=chunks,
    )
    recorder = PolicyRecorder(enabled)
    with bind_dependencies(deps), bind_trace_observer(recorder.observe):
        first = a._linked_screen_children("合成の質問", [], ranked, settings())
        second = a._linked_screen_children("合成の質問", [], ranked, settings())
    assert len(model_calls) == 1
    assert [item.chunk_uid for item in first[0]] == [item.chunk_uid for item in second[0]] == ["new"]
    calls = recorder.calls[("stage", "画面の選択")]
    if not enabled:
        assert all(call["input"] == call["output"] == {} for call in calls)
        return
    assert calls[0]["input"]["目録"] == {screen[0]: [screen[1]]}
    for index, call in enumerate(calls):
        output = json.loads(json.dumps(call["output"]))
        assert output["選択結果の再利用"] is bool(index)
        assert [item["ID"] for item in output["追加候補"]] == ["new"]
        assert [item["ID"] for item in output["展開用の候補"]] == ["old", "new", "parent"]
        assert output["候補の取得元"] == "injected"


@pytest.mark.parametrize("failed", [True, False])
def test_screen_failure_is_distinct_from_empty_selection(failed):
    def parse(*args, **kwargs):
        if failed:
            raise RuntimeError("synthetic")
        return ScreenLinks(candidates=[])

    deps = AnswerDependencies(
        search=unused,
        check_ready=unused,
        rerank=unused,
        parse_images=unused,
        parse_text=parse,
        screen_catalog=lambda **kwargs: {"synthetic.txt": ["（1）合成"]},
    )
    recorder = Recorder()
    with bind_dependencies(deps), bind_trace_observer(recorder.observe):
        assert a._linked_screen_children("合成の質問", [], [], settings()) == ([], [], [])
    output = recorder.stage("画面の選択")["output"]
    assert output["選択状態"] == ("failed" if failed else "no_match")
    assert output["選択した画面"] == (None if failed else [])


@pytest.mark.parametrize("source", ["process", "shared"])
def test_article_selection_records_loaded_marked_added_and_real_cache(tmp_path, source):
    model_calls = []
    loaded = [
        _stored("old", "synthetic.txt", article={"order": 0, "document": "synthetic.txt", "title": "合成A"}),
        _stored("new", "synthetic.txt", article={"order": 1, "document": "synthetic.txt", "title": "合成B"}),
        _stored("parent", "synthetic.txt", level="parent"),
    ]
    ranked = [a._stored_chunk_answer_record(loaded[0])]

    def parse(*args, **kwargs):
        model_calls.append(args)
        return ArticleIssueSelection(issues=[ArticleIssue(issue="合成", lines=[1, 2])])

    def chunks(*, articles, settings):
        assert [item.section_id for item in articles] == ["s1", "s2"]
        return loaded

    deps = AnswerDependencies(
        search=unused,
        check_ready=unused,
        rerank=unused,
        parse_images=unused,
        parse_text=parse,
        article_catalogs=lambda **kwargs: [CATALOG],
        article_chunks=chunks,
    )
    options = settings(article_catalog_cache_dir=tmp_path)
    recorder = Recorder()
    with bind_dependencies(deps), bind_trace_observer(recorder.observe):
        first = a._catalog_article_children("合成の質問", ranked, options)
        if source == "shared":
            a._ARTICLE_SELECTION_CACHE.clear()
        second = a._catalog_article_children("合成の質問", ranked, options)
    assert len(model_calls) == 1
    assert [item.chunk_uid for item in first[0]] == [item.chunk_uid for item in second[0]] == ["old", "new"]
    assert [item.chunk_uid for item in first[1]] == ["old", "new", "parent"]
    calls = recorder.calls[("stage", "条の選択")]
    assert calls[0]["input"]["目録"][0]["section_ids"] == ("root", "s1", "s2")
    assert calls[0]["output"]["選択の取得元"] == {"llm": 1}
    assert calls[1]["output"]["選択の取得元"] == {source: 1}
    output = json.loads(json.dumps(calls[1]["output"]))
    assert output["既存候補と重複する読取 ID"] == ["old"]
    assert output["読取数"] == 3 and output["追加候補数"] == 1
    assert output["Chunkを読み込んだ"] is True


@pytest.mark.parametrize(
    "mode,reason",
    [
        ("missing", "dependencies_unavailable"),
        ("documents", "document_limit_zero"),
        ("articles", "article_limit_zero"),
        ("catalog", "no_catalog"),
    ],
)
def test_article_early_exit_records_exact_skip_and_preserves_candidates(mode, reason):
    read = []
    deps = AnswerDependencies(
        search=unused,
        check_ready=unused,
        rerank=unused,
        parse_images=unused,
        parse_text=unused,
        article_catalogs=lambda **kwargs: read.append(kwargs) or [],
        article_chunks=unused,
    )
    ranked = [a._stored_chunk_answer_record(_stored("old", "synthetic.txt"))]
    options = settings(
        **(
            {"article_catalog_max_documents": 0}
            if mode == "documents"
            else {"article_catalog_max_articles": 0}
            if mode == "articles"
            else {}
        )
    )
    recorder = Recorder()
    completed = []
    with (
        bind_dependencies(deps) if mode != "missing" else nullcontext(),
        bind_trace_observer(recorder.observe),
        bind_step_listener(lambda name, state, elapsed, record: completed.append(record) if state != "started" else None),
    ):
        result = a._catalog_article_children("合成の質問", ranked, options)
    assert result == (ranked, [])
    output = recorder.stage("条の選択")["output"]
    assert output["理由"] == reason and output["選択状態"] == "skipped"
    assert completed[-1]["status"] == "skipped"
    assert bool(read) is (mode == "catalog")


@pytest.mark.parametrize("failed", [True, False])
def test_executed_article_selection_empty_is_not_skipped(failed):
    def parse(*args, **kwargs):
        if failed:
            raise RuntimeError("synthetic")
        return ArticleIssueSelection(issues=[])

    deps = AnswerDependencies(
        search=unused,
        check_ready=unused,
        rerank=unused,
        parse_images=unused,
        parse_text=parse,
        article_catalogs=lambda **kwargs: [CATALOG],
        article_chunks=unused,
    )
    recorder = Recorder()
    with bind_dependencies(deps), bind_trace_observer(recorder.observe):
        assert a._catalog_article_children("合成の質問", [], settings()) == ([], [])
    output = recorder.stage("条の選択")["output"]
    assert output["選択状態"] == ("failed" if failed else "no_match")
    assert output["Chunkを読み込んだ"] is False


def test_payload_factory_is_not_called_without_an_observer():
    with _execution_step("合成の工程") as step:
        step.input(unused)
        step.output(unused)
