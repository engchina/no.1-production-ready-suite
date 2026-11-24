"""合成の原文で Wiki の要求範囲、入力上限、再開を検証する。"""

import json
from collections.abc import Sequence
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pr_backend_core.bounded_calls import BoundedCaller
from pydantic import ValidationError
from rag_parser_core.extraction import DocumentElement, StructuredExtraction
from rag_parser_core.oci_enterprise_ai import OciEnterpriseAiConfig, measure_llm_request

from app.rag.wiki_compiler import WIKI_CONTEXT_PROMPT, compile_pages
from app.rag.wiki_large_unit import compile_large_unit
from app.rag.wiki_models import WikiPage, WikiUnit, fingerprint
from app.rag.wiki_packet import SourceContext, WikiRequest
from app.rag.wiki_paragraphs import Paragraph, SentenceTooLarge
from app.rag.wiki_prepare import WikiRequestBuilder
from app.rag.wiki_source_context import bind_paragraphs
from tests.test_wiki_compiler import units


def _builder(
    originals: list[WikiUnit],
    *,
    config: OciEnterpriseAiConfig | None = None,
    related: dict[str, list[str]] | None = None,
) -> WikiRequestBuilder:
    return WikiRequestBuilder(
        originals,
        config=config or OciEnterpriseAiConfig(default_model_id="gpt-5.5"),
        system_prompt=WIKI_CONTEXT_PROMPT,
        sources={},
        related=related or {},
        max_source_chars=120000,
        batch_size=4,
    )


def _item(uid: str, **fields: Any) -> dict[str, Any]:
    return (
        dict(
            unit_id=uid, context="合成の案内", possible_questions=[], dependencies=[], unresolved=[]
        )
        | fields
    )


@pytest.mark.parametrize("api", ["responses", "anthropic_messages"])
def test_large_catalog_only_includes_scoped_references_within_complete_wire_budget(
    api: str,
) -> None:
    base = units()[0]
    visible = [base.model_copy(update={"unit_id": f"long-source-key-{i:064d}"}) for i in range(600)]
    first, remote = visible[0], visible[-1]
    stale = base.model_copy(update={"unit_id": "retired", "chunk_set_id": "prior"})
    config = OciEnterpriseAiConfig(
        default_model_id="gpt-5.5" if api == "responses" else "claude-opus-4-6",
        oci_enterprise_ai_model_api=api,
        text_max_input_tokens=35000,
    )
    builder = _builder(
        [*visible, stale], config=config, related={first.unit_id: [stale.unit_id, remote.unit_id]}
    )
    packet = builder([first], True)
    payload = json.loads(packet.text)
    selected = packet.handles.real([row[0] for row in payload["outline"]])
    assert selected[:2] == [first.unit_id, remote.unit_id] and len(selected) <= 5
    assert stale.unit_id not in selected and not payload["document_complete"]
    schema = packet.schema.model_json_schema()
    assert "long-source-key-" not in packet.text + json.dumps(schema)
    measured = measure_llm_request(
        config,
        "",
        packet.text,
        system_prompt=WIKI_CONTEXT_PROMPT,
        response_schema=schema,
        response_schema_name="wiki_contexts",
    )
    assert measured.fits and measured.limit_tokens == 35000
    assert packet.estimated_tokens == measured.estimated_tokens


async def test_partial_checkpoint_resumes_only_remaining_row() -> None:
    originals = units()
    builder = _builder(originals)
    calls: list[int] = []
    saved: list[WikiPage] = []

    async def generate(prompt: str, text: str, **kwargs: Any) -> str:
        ids = json.loads(text)["target_unit_ids"]
        calls.append(len(ids))
        if len(calls) == 2:
            raise TimeoutError("中断")
        rows = [_item(uid) for uid in ids]
        if len(calls) == 1:
            rows[-1]["possible_questions"] = ["一", "二", "三"]
        return json.dumps({"items": rows})

    async def checkpoint(pages: Sequence[WikiPage]) -> None:
        saved.extend(pages)

    model = AsyncMock()
    model.generate.side_effect = generate
    options: dict[str, Any] = dict(
        model=model,
        max_input_chars=120000,
        batch_size=4,
        prepare=builder,
        preparation_fingerprint=builder.preparation_fingerprint,
        checkpoint=checkpoint,
        authorize=AsyncMock(),
    )
    with pytest.raises(TimeoutError):
        await compile_pages(originals, **options)
    assert [p.unit for p in saved] == originals[:3]
    result = await compile_pages(originals, saved=list(saved), **options)
    assert [p.unit for p in result] == originals and calls == [4, 1, 1]
    assert await compile_pages(originals, saved=result, **options) == result and calls == [4, 1, 1]


def test_partial_rows_require_unique_valid_handles_and_preserve_surrounding_text() -> None:
    originals = units()
    body = "原文の U1 はそのまま使う。"
    originals[0] = originals[0].model_copy(update={"text": body, "source_hash": fingerprint(body)})
    packet = _builder(originals)(originals, False)
    prefix = "U1 " + "あ" * 411
    raw = prefix + " U2 終"
    rows = [
        _item("U1"),
        _item("U1"),
        _item("U2", context=raw),
        _item("U3", dependencies=[{"unit_id": "missing", "relation": "reference"}]),
    ]
    (p,) = packet.resolve(json.dumps({"items": rows}), allow_partial=True)
    assert p.unit_id == originals[1].unit_id and len(p.context) <= 420
    assert (
        p.context.startswith(prefix + " ") and p.context.endswith(" 終") and "U2" not in p.context
    )
    with pytest.raises(ValidationError):
        packet.resolve(json.dumps({"items": rows, "extra": True}), allow_partial=True)


def test_one_source_paragraph_serves_overlapping_virtual_parent_chunks() -> None:
    text = "合成の段落から二つの範囲を読む。"
    parts = (text[:6], text[4:10])
    ids = ("first", "second")
    combined = "\n\n".join(parts)
    source = units()[0].model_copy(
        update={
            "source_chunk_ids": list(ids),
            "source_chunk_hashes": {
                key: fingerprint(part) for key, part in zip(ids, parts, strict=True)
            },
            "text": combined,
            "source_hash": fingerprint(combined),
        }
    )
    extracted = StructuredExtraction(
        raw_text=text,
        elements=[DocumentElement(element_id="e", text=text)],
        parser_artifacts={"xml_tree_json_format_version": 1},
    )
    offsets = {"first": (0, 6), "second": (4, 10)}
    bound = bind_paragraphs([source], extracted, offsets)[source.unit_id]
    bound.verify(source)
    assert bound.text == text and bound.ranges == ((0, 6), (4, 10))
    assert not bind_paragraphs([source], extracted, {**offsets, "second": (5, 10)})


async def test_large_unit_uses_sentence_ends_and_reassembles_one_page() -> None:
    text = "前段。中段。後段。"
    source = units()[0].model_copy(update={"text": text, "source_hash": fingerprint(text)})
    bound = SourceContext(
        source.document_id, source.chunk_set_id, source.source_hash, text, (), ((0, len(text)),)
    )
    fragments: list[str] = []

    async def generate(packet: WikiRequest) -> str:
        data = json.loads(packet.text)
        if "original_units" in data:
            fragments.append(data["original_units"][0][1])
        return json.dumps({"items": [_item("U1")]})

    save = AsyncMock()
    options: dict[str, Any] = dict(
        limit=96000,
        max_source_chars=4,
        measure=lambda t, s: len(t.encode()),
        generate=generate,
        authorize=AsyncMock(return_value={source.unit_id: source}),
        checkpoint=save,
        input_fingerprint="synthetic",
    )
    page, spans, _ = await compile_large_unit(
        source, bound, [Paragraph(0, len(text), (3, 6))], **options
    )
    assert (
        fragments == ["前段。", "中段。", "後段。"] and "".join(text[a:b] for a, b in spans) == text
    )
    assert page.unit == source
    save.assert_awaited_once_with(page)
    with pytest.raises(SentenceTooLarge) as raised:
        await compile_large_unit(source, bound, [Paragraph(0, len(text), source="e")], **options)
    assert (raised.value.source, raised.value.start, raised.value.end) == ("e", 0, len(text))
    assert len(fragments) == 3 and save.await_count == 1


@pytest.mark.parametrize("invalid_once", [None, "original_units", "fragment_navigation"])
async def test_large_unit_skips_an_oversized_related_title_and_keeps_later_references(
    invalid_once: str | None,
) -> None:
    base, long_title, later, *_ = units()
    text = "前段。後段。"
    unit = base.model_copy(
        update={
            "title": "装置 U2",
            "section_path": ["区画 U1"],
            "text": text,
            "source_hash": fingerprint(text),
        }
    )
    # 1 文字 = 1 token と数え、関連の題だけで上限 35000 の 3/4 を超える(#5813)。
    long_title = long_title.model_copy(update={"title": "大" * 40000})
    data = StructuredExtraction(raw_text=text, elements=[DocumentElement(text=text)])
    sources = bind_paragraphs([unit], data, {unit.source_chunk_ids[0]: (0, len(text))})
    builder = WikiRequestBuilder(
        [unit, long_title, later],
        config=OciEnterpriseAiConfig(default_model_id="gpt-5.5", text_max_input_tokens=35000),
        system_prompt=WIKI_CONTEXT_PROMPT,
        sources=sources,
        related={unit.unit_id: [long_title.unit_id, later.unit_id]},
        max_source_chars=3,
        batch_size=4,
    )
    originals = []
    invalid_input = None

    async def generate(prompt: str, context: str, **kwargs: Any) -> str:
        nonlocal invalid_input
        request = json.loads(context)
        assert request["related_titles"] == {"U1": unit.title, "U2": later.title}
        assert builder.measure(context, kwargs["response_schema"]) <= builder.token_limit
        if invalid_once in request and invalid_input is None:
            invalid_input = context
            return '{"items":[]}'
        if "original_units" in request:
            originals.append(request["original_units"][0][1])
        else:
            assert len(request["fragment_navigation"]) == 2
            assert {f["context"] for f in request["fragment_navigation"]} == {"区画 U1 の装置 U2"}
        return json.dumps(
            {
                "items": [
                    _item(
                        "U1",
                        context="区画 U1 の装置 U2",
                        dependencies=[dict(unit_id="U2", relation="reference")],
                    )
                ]
            }
        )

    model, save = AsyncMock(), AsyncMock()
    caller = BoundedCaller("wiki_build", sleep=AsyncMock())
    model.generate.side_effect = generate
    (page,) = await builder.compile(
        [unit], model=model, saved=[], authorize=AsyncMock(), checkpoint=save, caller=caller
    )
    assert originals == ["前段。", "後段。"]
    assert model.generate.await_count == (4 if invalid_once else 3)
    if invalid_once:
        assert [call.args[1] for call in model.generate.await_args_list].count(invalid_input) == 2
    assert page.navigation.context == "区画 U1 の装置 U2"
    assert [d.unit_id for d in page.navigation.dependencies] == [later.unit_id]
    save.assert_awaited_once_with([page])


async def test_queued_attempt_rechecks_the_revision_before_dispatch() -> None:
    original = units()[:1]
    builder = _builder(original)
    authorized = True

    async def check() -> None:
        if not authorized:
            raise PermissionError("原文の版が変わりました。")

    async def queued(operation: Any, **kwargs: Any) -> Any:
        nonlocal authorized
        authorized = False
        return await operation()

    caller, model, save = AsyncMock(), AsyncMock(), AsyncMock()
    caller.call.side_effect = queued
    with pytest.raises(PermissionError):
        await compile_pages(
            original,
            model=model,
            batch_size=1,
            max_input_chars=120000,
            prepare=builder,
            preparation_fingerprint=builder.preparation_fingerprint,
            caller=caller,
            authorize=check,
            checkpoint=save,
        )
    model.generate.assert_not_awaited()
    save.assert_not_awaited()
