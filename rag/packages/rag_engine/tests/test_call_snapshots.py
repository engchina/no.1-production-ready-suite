"""SDK 境界で要求・応答を記録する合成回帰。"""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from rag_engine.adapters import oci
from rag_engine.config import get_settings
from rag_engine.models.llm import TextOutput
from rag_engine.resources.tracing import bind_trace_observer


@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("failed", [False, True])
def test_sdk_boundary_records_private_request_schema_and_response(monkeypatch, streamed, failed):
    recorded = []
    sent = []
    response = SimpleNamespace(status="completed", output_parsed=TextOutput(text="合成の結果"), usage=None)

    class Observation:
        def usage(self, value):
            pass

        def request(self, value, *, schema=None):
            recorded.append(("request", value, schema))

        def response(self, value):
            recorded.append(("response", value))

    @contextmanager
    def observer(operation, model):
        assert (operation, model) == ("chat", "synthetic-model")
        yield Observation()

    def parse(**request):
        sent.append(request)
        if failed:
            raise RuntimeError("合成の接続失敗")
        return response

    def stream(client, request, settings, limits, **kwargs):
        sent.append(request)
        if failed:
            raise RuntimeError("合成の接続失敗")
        return response, None

    client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    monkeypatch.setattr(oci, "_stall_detection_enabled", lambda _: streamed)
    monkeypatch.setattr(oci, "_stall_limits", lambda *_: None)
    monkeypatch.setattr(oci, "_openai_client_for_provider", lambda *_: client)
    monkeypatch.setattr(oci, "_openai_client_with_deadline", lambda client, _: client)
    monkeypatch.setattr(oci, "_stream_parsed", stream)
    provider = SimpleNamespace(model="synthetic-model", label="synthetic", reasoning_effort="", base_url="https://synthetic.invalid")
    messages = [{"role": "user", "content": "合成の入力"}]
    with bind_trace_observer(observer):
        if failed:
            with pytest.raises(RuntimeError, match="合成の接続失敗"):
                oci._openai_parsed(messages, get_settings(environ={}, dotenv_path=None), TextOutput, provider)
        else:
            oci._openai_parsed(messages, get_settings(environ={}, dotenv_path=None), TextOutput, provider)
    assert len(sent) == 1
    assert [item[0] for item in recorded] == (["request"] if failed else ["request", "response"])
    request, schema = recorded[0][1:]
    assert request["input"] == messages
    assert request["model"] == provider.model
    assert "text_format" not in request
    assert request["text"]["format"] == schema
    assert schema["strict"] is True and schema["schema"]["properties"]["text"]["type"] == "string"
    assert sent[0]["text_format"] is TextOutput
    if not failed:
        assert recorded[1][1] is response
