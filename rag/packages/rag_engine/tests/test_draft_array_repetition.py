"""長い同一計画の反復だけを途中で止め、通常のJSON・無効化時を維持する。"""

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from test_llm_runaway_output import PROVIDER, _RecordingClient, _text_script

from rag_engine.adapters import oci
from rag_engine.config import get_settings
from rag_engine.models.llm import GroundedDraft
from rag_engine.resources.deadline import Deadline, bind_deadline
from tests.test_llm_stall_resend import USAGE, _Clock


def entries(*, distinct=False):
    return [
        {"request_id": f"R{index if distinct else 1}", "evidence_scope": [{"evidence_id": "E1", "target": "合成の説明。" * 100}]}
        for index in range(64)
    ]


@pytest.mark.parametrize("size", [1, 17, 4096])
def test_long_repeated_objects_are_cut_before_the_full_plan(size):
    text = json.dumps({"request_coverage": entries()}, ensure_ascii=False)
    monitor = oci._RunawayMonitor(draft_arrays=True)
    cut = None
    for offset in range(0, len(text), size):
        if monitor.feed(text[offset : offset + size]):
            cut = offset + size
            break
    assert cut is not None and cut < len(text) // 2
    # 旧来の短い周期の検出には該当しない。
    assert oci._RunawayMonitor().feed(text) is None


def test_different_entries_other_arrays_and_json_strings_are_not_cut():
    for data in [{"request_coverage": entries(distinct=True)}, {"rows": entries()}, {"summary": json.dumps({"items": entries()})}]:
        assert oci._RunawayMonitor(draft_arrays=True).feed(json.dumps(data, ensure_ascii=False)) is None


@pytest.mark.parametrize("enabled", [True, False])
def test_streaming_retries_only_when_existing_guard_is_enabled(enabled):
    clock = _Clock()
    chunks = ['{"request_coverage":['] + [json.dumps(row, ensure_ascii=False) + "," for row in entries()]
    final = SimpleNamespace(
        type="response.completed",
        response=SimpleNamespace(status="completed", output_parsed=GroundedDraft(items=[], summary="", confidence="low"), usage=USAGE),
    )
    scripts = [_text_script(chunks, final=final)]
    if enabled:
        scripts.append(_text_script(['{"items":[]}'], final=final))
    client = _RecordingClient(clock, scripts)
    settings = replace(get_settings(environ={}, dotenv_path=None), llm_runaway_detection=enabled, llm_stall_detection=True)
    with (
        patch.object(oci.time, "monotonic", clock),
        patch.object(oci, "_openai_client_for_provider", return_value=client),
        bind_deadline(Deadline(600)),
    ):
        result = oci._openai_parsed([], settings, GroundedDraft, PROVIDER)
    assert not result.items
    assert len(client.streams) == (2 if enabled else 1)
    assert all(stream.closed for stream in client.streams)
