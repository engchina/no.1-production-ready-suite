"""構造化出力の JSON の文字列の外の空白の連続だけを打ち切って送り直す（#5077）。

文字列の中（回答の本文）の空白・反復した表は打ち切らない（#5041）。出力はすべて合成の録画で、実サービスは呼ばない。
"""

import json
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from test_llm_stall_resend import PROVIDER, USAGE, _Clock

from rag_engine.adapters import oci
from rag_engine.config import get_settings
from rag_engine.generation.citation_unit_ids import SentUnitCatalog
from rag_engine.generation.full_answer_wire import full_answer_model
from rag_engine.resources.deadline import Deadline, bind_deadline
from rag_engine.resources.llm_usage import LlmUsage, llm_usage

LIMIT = 1000
# 「confidence」の値の前で空白と改行だけが続いた出力（出力の上限まで終わらない）。
STALLED_PREFIX = '{"answer_body": "合成の本文", "response_kind": "answered",\n  "confidence":'
# 本文の中のエスケープしていない引用符で文字列が閉じ、空白とタブが続いた後に完了した出力。
CLOSED_EARLY = '{"answer_body": "合成の本文の「`"' + " \t" * 1116 + ',\n  "response_kind": "answered",\n  "confidence": "high"\n}'


def _schema():
    return full_answer_model(SentUnitCatalog([]))


def _valid_raw(body: str = "合成の本文") -> str:
    return _schema()(answer_body=body, response_kind="answered", confidence="high").model_dump_json()


def _chunks(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


class _CountingStream:
    """yield した event の数と、閉じたかを記録する。"""

    def __init__(self, clock: _Clock, events) -> None:
        self.clock, self.events, self.started = clock, events, clock.now
        self.yielded = 0
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.closed = True

    def __iter__(self):
        for at, event in self.events:
            self.clock.now = self.started + at
            self.yielded += 1
            yield event


class _StreamClient:
    def __init__(self, clock: _Clock, scripts) -> None:
        self.clock, self.scripts = clock, list(scripts)
        self.streams: list[_CountingStream] = []
        self.responses = SimpleNamespace(create=self._create)

    def with_options(self, **_options):
        return self

    def _create(self, **kwargs):
        assert kwargs["stream"] is True
        stream = _CountingStream(self.clock, self.scripts.pop(0))
        self.streams.append(stream)
        return stream


def _script(text: str, *, status: str = "completed", chunk: int = 64):
    events = [(0.01, SimpleNamespace(type="response.created"))]
    parts = _chunks(text, chunk)
    events.extend((0.02 + i * 0.001, SimpleNamespace(type="response.output_text.delta", delta=part)) for i, part in enumerate(parts))
    final = SimpleNamespace(
        status=status,
        output_text=text,
        usage=USAGE,
        incomplete_details=SimpleNamespace(reason="max_output_tokens") if status == "incomplete" else None,
    )
    events.append((0.03 + len(parts) * 0.001, SimpleNamespace(type=f"response.{status}", response=final)))
    return events


def _settings(**changes):
    base = get_settings(environ={}, dotenv_path=None)
    values = {"llm_stall_detection": True, "llm_request_timeout_seconds": 60, "llm_json_whitespace_run_limit": LIMIT, **changes}
    return replace(base, **values)


def _call(client, settings, *, streamed: bool = True):
    usage = LlmUsage()
    token = llm_usage.set(usage)
    clock = client.clock if hasattr(client, "clock") else _Clock()
    try:
        with (
            patch.object(oci.time, "monotonic", clock),
            patch.object(oci.time, "sleep", lambda _seconds: None),
            patch.object(oci, "_openai_client_for_provider", return_value=client),
            bind_deadline(Deadline(60)) if streamed else nullcontext(),
        ):
            result = oci._parse_with_retry(lambda: oci._openai_parsed([], settings, _schema(), PROVIDER), "test", settings)
    finally:
        llm_usage.reset(token)
    return result, usage.snapshot()


# ---- 字句の状態 ----


@pytest.mark.parametrize("chunk", [1, 7, 64, 100_000], ids=["c1", "c7", "c64", "whole"])
def test_whitespace_outside_strings_reaches_the_limit_at_any_chunk_size(chunk):
    scanner = oci._JsonWhitespaceRun(LIMIT)
    assert any(scanner.feed(part) for part in _chunks(STALLED_PREFIX + " \n" * LIMIT, chunk))


@pytest.mark.parametrize("chunk", [1, 7, 64, 100_000], ids=["c1", "c7", "c64", "whole"])
def test_whitespace_and_repeated_rows_inside_strings_are_not_counted(chunk):
    body = "始め" + " " * 5000 + "\n" + "| same | same |\n" * 300 + "\t" * 3000 + "終わり"
    scanner = oci._JsonWhitespaceRun(LIMIT)
    assert not any(scanner.feed(part) for part in _chunks(_valid_raw(body), chunk))


def test_escaped_quote_does_not_end_the_string():
    # `\"` の後の空白は文字列の中。
    raw = json.dumps({"answer_body": 'a "quoted" word' + " " * 3000 + "end"}, ensure_ascii=False)
    assert '\\"' in raw
    assert not oci._JsonWhitespaceRun(LIMIT).feed(raw)


def test_escaped_backslash_before_quote_ends_the_string():
    # `\\"` は「エスケープした \ ＋文字列の終わり」なので、続く空白は文字列の外。
    assert oci._JsonWhitespaceRun(LIMIT).feed('{"a": "x\\\\"' + " " * LIMIT)


def test_pretty_printed_json_is_not_cut():
    nested: dict = {"leaf": "v"}
    for _ in range(40):
        nested = {"k": nested, "list": [1, 2, 3]}
    assert not oci._JsonWhitespaceRun(LIMIT).feed(json.dumps(nested, indent=4))


# ---- streaming の呼び出し ----


def test_stream_whitespace_loop_after_a_key_is_cut_early_and_resent():
    clock = _Clock()
    stalled = _script(STALLED_PREFIX + " \n" * 3709, status="incomplete")
    client = _StreamClient(clock, [stalled, _script(_valid_raw())])
    result, usage = _call(client, _settings())
    assert result.answer_body == "合成の本文"
    assert len(client.streams) == 2 and all(stream.closed for stream in client.streams)
    # 出力の上限（未完了の event）まで読まずに打ち切った。
    first = client.streams[0]
    assert first.yielded < len(stalled) - 1
    assert usage["calls"] == 2


def test_stream_string_closed_early_then_whitespace_is_resent_instead_of_accepted():
    clock = _Clock()
    assert json.loads(CLOSED_EARLY)["answer_body"] == "合成の本文の「`"  # JSON としては有効
    client = _StreamClient(clock, [_script(CLOSED_EARLY), _script(_valid_raw())])
    result, _usage = _call(client, _settings())
    assert result.answer_body == "合成の本文"
    assert len(client.streams) == 2
    assert client.streams[0].yielded < len(_script(CLOSED_EARLY)) - 1


@pytest.mark.parametrize("chunk", [1, 32, 10_000], ids=["c1", "c32", "whole"])
def test_stream_long_whitespace_and_escaped_quotes_inside_the_body_are_kept(chunk):
    body = 'a "quoted" word' + " " * 3000 + "\n" + "| same | same |\n" * 200 + "end"
    clock = _Clock()
    client = _StreamClient(clock, [_script(_valid_raw(body), chunk=chunk)])
    result, usage = _call(client, _settings())
    assert result.answer_body == body
    assert len(client.streams) == 1
    assert usage["calls"] == 1


def test_stream_whitespace_loop_fails_as_invalid_output_when_retries_are_used_up():
    clock = _Clock()
    loop = STALLED_PREFIX + " " * 5000
    client = _StreamClient(clock, [_script(loop, status="incomplete"), _script(loop, status="incomplete")])
    with pytest.raises(oci.LlmJsonWhitespaceRun) as failure:
        _call(client, _settings())
    assert oci.is_invalid_structured_output(failure.value)
    assert len(client.streams) == 2


def test_limit_comes_from_settings():
    clock = _Clock()
    text = STALLED_PREFIX + " " * 300 + ' "high"}'
    assert json.loads(text)["confidence"] == "high"
    client = _StreamClient(clock, [_script(text), _script(_valid_raw())])
    _result, usage = _call(client, _settings(llm_json_whitespace_run_limit=200))
    assert usage["calls"] == 2
    client = _StreamClient(_Clock(), [_script(text)])
    _result, usage = _call(client, _settings())
    assert usage["calls"] == 1


# ---- streaming しない呼び出し ----


@pytest.mark.parametrize("status", ["completed", "incomplete"])
def test_nonstream_output_with_whitespace_run_is_resent_with_the_same_budget(status):
    requests = []
    bad = CLOSED_EARLY if status == "completed" else STALLED_PREFIX + " \n" * 3709
    responses = [
        SimpleNamespace(
            status=status,
            output_text=bad,
            usage=USAGE,
            incomplete_details=SimpleNamespace(reason="max_output_tokens") if status == "incomplete" else None,
        ),
        SimpleNamespace(status="completed", output_text=_valid_raw(), usage=USAGE),
    ]

    def parse(**request):
        requests.append(request)
        return responses.pop(0)

    client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    client.with_options = lambda **_kwargs: client
    settings = replace(get_settings(environ={}, dotenv_path=None), llm_json_whitespace_run_limit=LIMIT)
    with oci.bind_output_cap(100):
        result, usage = _call(client, settings, streamed=False)
    assert result.answer_body == "合成の本文"
    assert len(requests) == 2
    # 出力の上限を広げる送り直しではない。
    assert requests[0]["max_output_tokens"] == requests[1]["max_output_tokens"]
    assert usage["calls"] == 2
