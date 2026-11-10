"""画面目録の選択・再利用・実読取と返却候補を合成データで確認する。"""

import json
from contextlib import contextmanager, nullcontext
from dataclasses import replace

import pytest
from test_query_stage_io import Recorder
from test_screen_catalog import _stored as screen_row

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import answering as a
from rag_engine.generation.execution_record import _execution_step, bind_step_listener
from rag_engine.resources.tracing import bind_trace_observer
from rag_engine.retrieval.screen_catalog import LinkedScreen, ScreenLinks


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
    return replace(get_settings(environ={}, dotenv_path=None), **values)


def unused(*args, **kwargs):
    raise AssertionError("unexpected I/O")


@pytest.fixture(autouse=True)
def clear_caches():
    for cache in [a._SCREEN_CATALOG_CACHE, a._SCREEN_LINK_CACHE]:
        cache.clear()
    yield
    for cache in [a._SCREEN_CATALOG_CACHE, a._SCREEN_LINK_CACHE]:
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


def test_payload_factory_is_not_called_without_an_observer():
    with _execution_step("合成の工程") as step:
        step.input(unused)
        step.output(unused)


def test_empty_screen_catalog_is_skipped_without_a_model_call():
    deps = AnswerDependencies(
        search=unused,
        check_ready=unused,
        rerank=unused,
        parse_images=unused,
        parse_text=unused,
        screen_catalog=lambda **kwargs: {},
    )
    recorder = Recorder()
    events = []
    with (
        bind_dependencies(deps),
        bind_trace_observer(recorder.observe),
        bind_step_listener(lambda name, state, elapsed, record: events.append((state, record))),
    ):
        assert a._linked_screen_children("合成の質問", [], [], settings()) == ([], [], [])
    assert events[-1][0] == "skipped"
    assert events[-1][1]["llm_calls"] == 0
    assert recorder.stage("画面の選択")["output"]["理由"] == "catalog_empty"


def test_catalog_selection_failure_notifies_failed_instead_of_success():
    def parse(*args, **kwargs):
        raise RuntimeError("synthetic")

    deps = AnswerDependencies(
        search=unused,
        check_ready=unused,
        rerank=unused,
        parse_images=unused,
        parse_text=parse,
        screen_catalog=lambda **kwargs: {"synthetic.txt": ["（1）合成"]},
    )
    events = []
    with bind_dependencies(deps), bind_step_listener(lambda name, state, elapsed, record: events.append((name, state, record))):
        assert a._linked_screen_children("合成の質問", [], [], settings()) == ([], [], [])
    final = [event for event in events if event[0] == "画面の選択"][-1]
    assert final[1] == "error"
    assert final[2]["status"] == "failed"
