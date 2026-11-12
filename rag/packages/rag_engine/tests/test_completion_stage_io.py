"""追加検索と画像準備の入力・出力を実際の回答入口で照合する。"""

from dataclasses import replace

import pytest
from grounded_stub import echo_model
from test_answer_generation import _chunk_run_stub, _rerank_ready_settings
from test_guide_steps import context, parent, plan
from test_query_stage_io import Recorder

from rag_engine.generation import answering as a
from rag_engine.generation.answer_models import AnswerResponse
from rag_engine.knowledge.classification import ClassificationFilter
from rag_engine.resources.tracing import bind_trace_observer
from rag_engine.retrieval.text_search_tokenizer import TextSearchTokenizationResult


class PolicyRecorder(Recorder):
    def __init__(self, enabled=True):
        super().__init__()
        self.enabled = enabled

    def observe(self, *args):
        from contextlib import contextmanager

        @contextmanager
        def observe():
            with super(PolicyRecorder, self).observe(*args) as observation:
                observation.capture_enabled = self.enabled
                yield observation

        return observe()


def run(monkeypatch, recorder, *, question="合成の質問", guide=None, generate=False, complete=True, settings=None, search=None):
    original = context(parent("old", "合成本文"))
    monkeypatch.setattr(a, "load_latest_or_source_chunk_run", lambda *args, **kwargs: _chunk_run_stub())
    monkeypatch.setattr(a, "check_adb_hybrid_search_ready", lambda *args, **kwargs: None)
    monkeypatch.setattr(a, "build_adb_hybrid_answer_context", search or (lambda *args, **kwargs: original))
    monkeypatch.setattr(a, "parse_text_response", echo_model)
    with bind_trace_observer(recorder.observe):
        return a.answer_question_result(
            question,
            "synthetic-run",
            ["docling"],
            settings or _rerank_ready_settings(),
            guide_plan=guide,
            generate_answer=generate,
            complete_evidence=complete,
            classification_filter=ClassificationFilter(large_category="synthetic-category"),
        )


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("failed", [True, False])
def test_overflow_records_actual_returned_and_adopted_evidence(monkeypatch, enabled, failed):
    question = "対象は合成A。\n(1) 合成手順を教えてください。\n(2) 合成設定を教えてください。"
    calls = []
    original, extra = context(parent("old", "合成本文")), context(parent("new", "合成追加本文"))

    def search(text, *args, **kwargs):
        calls.append(text)
        if len(calls) > 1:
            assert recorder.calls[("stage", "検索上限の要求別補完")]
            if failed:
                raise ValueError("synthetic")
        return original if len(calls) == 1 else extra

    monkeypatch.setattr(
        a,
        "tokenize_text_search_query_with_trace",
        lambda text, **kwargs: TextSearchTokenizationResult(
            tokens=("合成",), oracle_text_query="{合成}", truncated_count=int(text == question)
        ),
    )
    recorder = PolicyRecorder(enabled)
    result = run(
        monkeypatch, recorder, question=question, search=search, settings=replace(_rerank_ready_settings(), extra_retrieval_max_queries=1)
    )
    assert len(calls) == 2
    call = recorder.stage("検索上限の要求別補完")
    if enabled:
        assert call["input"]["原質問"] == question
        assert call["input"]["分類条件"]["large_category"] == "synthetic-category"
        assert call["input"]["使用済みの要求別予算"]["queries"] == 0
        assert call["output"]["新規採用ID"] == ([] if failed else ["run:new"])
        assert call["output"]["検索結果"] == result.generation_trace["query_overflow"]
        if failed:
            assert call["output"]["要求ごとの返却根拠"] == []
            assert call["output"]["検索結果"]["requests"][0]["error_type"] == "ValueError"
        else:
            assert call["output"]["要求ごとの返却根拠"][0]["候補"][0]["ID"] == "run:new"
    else:
        assert call["input"] == call["output"] == {}


def test_no_overflow_has_real_skip_and_retains_context(monkeypatch):
    recorder = Recorder()
    result = run(monkeypatch, recorder)
    call = recorder.stage("検索上限の要求別補完")
    assert call["output"]["検索結果"]["triggered"] is False
    assert call["output"]["新規採用ID"] == []
    step = next(item for item in result.execution_steps if item["name"] == "検索上限の要求別補完")
    assert step["status"] == "skipped"
    assert [item["ID"] for item in call["output"]["処理後の根拠"]] == ["run:old"]


@pytest.mark.parametrize("enabled", [True, False])
def test_guide_actual_plan_settings_and_result_are_associated(monkeypatch, enabled):
    guide = plan({"id": "s1", "title": "合成の手順", "retrieval_hints": ["合成の検索語"]})
    recorder = Recorder()
    result = run(monkeypatch, recorder, guide=guide, settings=replace(_rerank_ready_settings(), guide_step_retrieval_enabled=enabled))
    call = recorder.stage("業務ガイドの手順ごとの検索")
    assert call["input"]["手順計画"]["guide_id"] == guide.guide_id
    assert call["input"]["設定"]["guide_step_retrieval_enabled"] is enabled
    assert call["output"]["検索と取得の記録"] == result.generation_trace["guide_steps"]
    assert call["output"]["手順と根拠の対応"][0][0] == "s1"
    step = next(item for item in result.execution_steps if item["name"] == "業務ガイドの手順ごとの検索")
    assert (step["status"] == "skipped") is (not enabled)
    assert call["input"]["既存根拠"][0]["ID"] == "run:old"


@pytest.mark.parametrize(
    "has_images,reads_images,available,reason",
    [
        (False, True, False, "no_image_evidence"),
        (True, False, False, "model_without_vision"),
        (True, True, False, "no_attachable_image"),
        (True, True, True, "attached"),
    ],
)
def test_image_preparation_records_identifiers_without_media_or_paths(monkeypatch, has_images, reads_images, available, reason):
    images = (
        (
            {
                "image_id": "img-a",
                "page": 2,
                "visual_kind": "diagram",
                "prompt_path": "private/image.png",
                "media": "private-media",
                "source": "private/source.pdf",
            },
        )
        if available
        else ()
    )
    prepared = []
    generated = []
    monkeypatch.setattr(a, "record_has_image_evidence", lambda record: has_images)
    monkeypatch.setattr(a, "answer_model_reads_images", lambda *args: reads_images)
    monkeypatch.setattr(a, "answer_image_evidence", lambda *args, **kwargs: prepared.append(True) or images)
    monkeypatch.setattr(a, "_image_prompt_mode", lambda *args: "vision_attachments")

    def generate(question, ctx, settings, **kwargs):
        generated.append(kwargs["image_evidence"])
        return a.GroundedAnswer(AnswerResponse(answer_text="合成回答"), ctx, tuple(kwargs["image_evidence"]), kwargs["image_prompt_mode"])

    monkeypatch.setattr(a, "synthesize_grounded_answer", generate)
    recorder = Recorder()
    run(monkeypatch, recorder, generate=True)
    call = recorder.stage("回答に添付する画像")
    assert call["input"]["画像を読める"] is reads_images
    assert call["output"]["理由"] == reason
    assert call["output"]["添付画像数"] == int(available)
    assert len(prepared) == int(has_images and reads_images)
    assert generated == [images]
    assert "private" not in str(call)
    if available:
        assert call["output"]["画像"] == [{"image_id": "img-a", "page": 2, "visual_kind": "diagram", "添付": True}]
