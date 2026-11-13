"""合法な反復表や空白を、本文の周期やdelta分割で打ち切らない。"""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from test_llm_stall_resend import PROVIDER, USAGE, _Client, _Clock

from rag_engine.adapters import oci
from rag_engine.config import get_settings
from rag_engine.generation.citation_unit_ids import SentUnitCatalog
from rag_engine.generation.full_answer_wire import full_answer_model
from rag_engine.resources.deadline import Deadline, bind_deadline
from rag_engine.resources.llm_usage import LlmUsage, llm_usage


@pytest.mark.parametrize("chunk_size", [1, 32, 256, 10000])
@pytest.mark.parametrize("kind", ["same_rows", "whitespace"])
def test_valid_full_answer_survives_repeated_content_and_all_chunk_sizes(chunk_size, kind):
    schema = full_answer_model(SentUnitCatalog([]))
    body = "| column A | column B |\n| --- | --- |\n" + "| same | same |\n" * 200 if kind == "same_rows" else "始め" + " " * 3000 + "終わり"
    expected = schema(answer_body=body, response_kind="answered", confidence="high", citations=[])
    raw = expected.model_dump_json()
    schema.model_validate_json(raw)
    chunks = [raw[i : i + chunk_size] for i in range(0, len(raw), chunk_size)]
    events = [(0.01, SimpleNamespace(type="response.created"))]
    events.extend((0.02 + i * 0.001, SimpleNamespace(type="response.output_text.delta", delta=chunk)) for i, chunk in enumerate(chunks))
    events.append(
        (
            0.03 + len(chunks) * 0.001,
            SimpleNamespace(type="response.completed", response=SimpleNamespace(status="completed", output_text=raw, usage=USAGE)),
        )
    )
    clock = _Clock()
    client = _Client(clock, [events])
    settings = replace(get_settings(environ={}, dotenv_path=None), llm_stall_detection=True, llm_request_timeout_seconds=60)
    usage = LlmUsage()
    token = llm_usage.set(usage)
    try:
        with (
            patch.object(oci.time, "monotonic", clock),
            patch.object(oci, "_openai_client_for_provider", return_value=client),
            bind_deadline(Deadline(60)),
        ):
            actual = oci._openai_parsed([], settings, schema, PROVIDER)
    finally:
        llm_usage.reset(token)
    assert actual.answer_body == body
    assert actual.model_dump() == expected.model_dump()
    assert len(client.streams) == 1 and client.streams[0].closed
    assert usage.snapshot()["calls"] == 1


def test_provider_output_limit_is_still_incomplete_and_not_a_success():
    clock = _Clock()
    final = SimpleNamespace(
        status="incomplete", incomplete_details=SimpleNamespace(reason="max_output_tokens"), output_text=" " * 3000, usage=USAGE
    )
    events = [
        (0.1, SimpleNamespace(type="response.output_text.delta", delta=" " * 3000)),
        (0.2, SimpleNamespace(type="response.incomplete", response=final)),
    ]
    client = _Client(clock, [events])
    settings = replace(get_settings(environ={}, dotenv_path=None), llm_stall_detection=True)
    schema = full_answer_model(SentUnitCatalog([]))
    with (
        patch.object(oci.time, "monotonic", clock),
        patch.object(oci, "_openai_client_for_provider", return_value=client),
        bind_deadline(Deadline(60)),
        pytest.raises(oci.IncompleteResponse) as failure,
    ):
        oci._openai_parsed([], settings, schema, PROVIDER)
    assert failure.value.reason == "max_output_tokens"
    assert len(client.streams) == 1 and client.streams[0].closed


def test_nonstream_output_limit_uses_provider_reason_instead_of_body_pattern():
    requests = []
    response = SimpleNamespace(
        status="incomplete", incomplete_details=SimpleNamespace(reason="max_output_tokens"), output_text="repeated " * 1000, usage=USAGE
    )

    def parse(**request):
        requests.append(request)
        return response

    client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    client.with_options = lambda **kwargs: client
    settings = get_settings(environ={}, dotenv_path=None)
    schema = full_answer_model(SentUnitCatalog([]))
    messages = [{"role": "user", "content": "合成の入力"}]
    with (
        patch.object(oci, "_openai_client_for_provider", return_value=client),
        oci.bind_output_cap(100),
        pytest.raises(oci.IncompleteResponse) as failure,
    ):
        oci._openai_parsed(messages, settings, schema, PROVIDER)
    assert failure.value.reason == "max_output_tokens"
    assert len(requests) == 1 and requests[0]["input"] == messages
