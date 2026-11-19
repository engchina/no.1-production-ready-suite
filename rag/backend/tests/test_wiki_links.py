"""文書をまたぐ Wiki の関連の構築（#5492）を、合成の案内と決定論のスタブで確かめる。

DB・外部モデルは使わない。実 Oracle の保存と無効化は test_wiki_store_oracle.py で確かめる。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.rag import wiki_jobs as module
from app.rag.request_context import AuditRequestContext
from app.rag.wiki_links import WikiLinkItem, judge_links, link_query_text
from app.rag.wiki_models import WikiContext, WikiPage, WikiUnit, fingerprint, link_schema


def page(doc: str, name: str, *, text: str | None = None) -> WikiPage:
    body = text or f"{name} の合成の本文。"
    unit = WikiUnit(
        unit_id=f"{doc}-{name}",
        document_id=doc,
        chunk_set_id=f"{doc}-cs",
        source_chunk_ids=[f"{doc}-{name}-chunk"],
        section_id="section",
        section_path=["合成の章"],
        title=name,
        text=body,
        source_hash=fingerprint(body),
    )
    return WikiPage(
        unit=unit,
        navigation=WikiContext(
            unit_id=unit.unit_id,
            context=f"{name} の案内",
            possible_questions=[f"{name} の条件は何か。"],
            dependencies=[],
            unresolved=[f"{name} の例外は別の通知で定める。"],
        ),
        input_fingerprint="v",
    )


class LinkModel:
    """各起点を最初の候補へ「例外」で結ぶ。``invalid`` なら候補に無い ID を返す。"""

    def __init__(self, *, invalid: bool = False, fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.invalid, self.fail = invalid, fail

    async def generate(self, prompt: str, context: str, **kwargs: Any) -> str:
        data = json.loads(context)
        self.calls.append(data)
        if self.fail:
            raise RuntimeError("合成の失敗")
        target = "C999" if self.invalid else data["candidates"][0]["unit_id"]
        return json.dumps(
            {
                "items": [
                    {
                        "unit_id": source["unit_id"],
                        "links": [{"unit_id": target, "relation": "exception", "reason": "合成"}],
                    }
                    for source in data["sources"]
                ]
            }
        )


async def allow() -> None:
    return None


def test_link_schema_allows_only_candidates_and_every_source_once() -> None:
    schema = link_schema(["S1", "S2"], ["C1"], max_links=1)
    link = {"unit_id": "C1", "relation": "exception", "reason": "合成"}
    schema.model_validate(
        {"items": [{"unit_id": "S1", "links": [link]}, {"unit_id": "S2", "links": []}]}
    )
    with pytest.raises(ValidationError):  # 候補に無い終点
        schema.model_validate(
            {
                "items": [
                    {"unit_id": "S1", "links": [{**link, "unit_id": "C2"}]},
                    {"unit_id": "S2", "links": []},
                ]
            }
        )
    with pytest.raises(ValidationError):  # 起点の欠落
        schema.model_validate({"items": [{"unit_id": "S1", "links": []}]})
    with pytest.raises(ValueError):  # 起点と候補の重なり（同じ単位を結ばない）
        link_schema(["S1"], ["S1"], max_links=1)


@pytest.mark.asyncio
async def test_judge_sends_short_ids_and_checkpoints_real_ids() -> None:
    source, other = page("doc-a", "a1", text="長い" * 100), page("doc-b", "b1")
    saved: list[tuple[list[str], list[str], list[tuple[str, str, str]]]] = []

    async def checkpoint(batch: Any, candidates: Any, decisions: Any) -> None:
        saved.append(
            (
                [item.source.unit.unit_id for item in batch],
                list(candidates),
                [(d.source_unit_id, d.target_unit_id, d.relation) for d in decisions],
            )
        )

    model = LinkModel()
    calls = await judge_links(
        [WikiLinkItem("forward:doc-a-a1", source, (other,))],
        model=model,
        batch_size=4,
        original_chars=20,
        max_input_chars=100000,
        authorize=allow,
        checkpoint=checkpoint,
    )
    assert calls == 1
    sent = json.dumps(model.calls, ensure_ascii=False)
    assert "doc-a" not in sent and "doc-b" not in sent
    assert model.calls[0]["sources"][0]["original_truncated"] is True
    assert len(model.calls[0]["sources"][0]["original"]) == 20
    assert saved == [(["doc-a-a1"], ["doc-b-b1"], [("doc-a-a1", "doc-b-b1", "exception")])]


@pytest.mark.asyncio
async def test_invalid_output_is_split_and_a_single_invalid_source_fails() -> None:
    items = [
        WikiLinkItem(f"forward:{name}", page("doc-a", name), (page("doc-b", "b1"),))
        for name in ("a1", "a2")
    ]
    model = LinkModel(invalid=True)
    with pytest.raises(ValidationError):
        await judge_links(
            items,
            model=model,
            batch_size=2,
            original_chars=100,
            max_input_chars=100000,
            authorize=allow,
            checkpoint=lambda *args: allow(),
        )
    # 2 件で不正 → 二分して 1 件ずつ取り直し、1 件でも不正なら失敗にする。
    assert [len(call["sources"]) for call in model.calls] == [2, 1]


@pytest.mark.asyncio
async def test_sources_and_candidates_must_not_overlap() -> None:
    a1, b1 = page("doc-a", "a1"), page("doc-b", "b1")
    with pytest.raises(ValueError):
        await judge_links(
            [WikiLinkItem("forward:a1", a1, (b1,)), WikiLinkItem("reverse:b1", b1, (a1,))],
            model=LinkModel(),
            batch_size=2,
            original_chars=100,
            max_input_chars=100000,
            authorize=allow,
            checkpoint=lambda *args: allow(),
        )


def test_query_text_uses_the_navigation_not_the_original() -> None:
    text = link_query_text(page("doc-a", "a1", text="原文だけの語"))
    assert "a1 の案内" in text and "例外は別の通知" in text and "原文だけの語" not in text


class LinkStore:
    """文書 A（構築した文書）と文書 B（既存の文書）の案内を持つ合成の保存先。"""

    def __init__(self) -> None:
        self.a = [page("doc-a", "a1"), page("doc-a", "a2")]
        self.b = [page("doc-b", "b1")]
        self.kbs = ["kb-a"]
        self.doc = {"status": "READY", "compiler_fingerprint": "compiler-a"}
        self.doc["source_fingerprint"] = "source-a"
        self.state: dict[str, Any] | None = None
        self.saved: list[dict[str, Any]] = []
        self.resolved: list[dict[str, str]] = []

    async def document_state(self, did: str) -> Any:
        return dict(self.doc)

    async def link_knowledge_bases(self, did: str) -> list[str]:
        return list(self.kbs)

    async def link_state(self, did: str) -> Any:
        return dict(self.state) if self.state else None

    async def claim_links(self, did: str, source: str, links: str, **kwargs: Any) -> Any:
        same = self.state and (
            self.state["source_fingerprint"],
            self.state["link_fingerprint"],
        ) == (
            source,
            links,
        )
        done = list(self.state.get("done", [])) if same and self.state else []
        self.state = {
            "source_fingerprint": source,
            "link_fingerprint": links,
            "status": "RUNNING",
            "leased": 1,
            "failure_code": None,
            "done": done,
        }
        return "lease", done

    async def save_links(self, did: str, token: str, **kwargs: Any) -> None:
        assert self.state is not None
        self.saved.append(kwargs)
        self.state["done"] = [*self.state["done"], *kwargs["done"]]

    async def finish_links(self, did: str, token: str, *, failure_code: str | None = None) -> None:
        assert self.state is not None
        self.state.update(
            status="FAILED" if failure_code else "READY", failure_code=failure_code, leased=0
        )

    async def resolve(self, filters: dict[str, str], compiler: str) -> Any:
        """範囲を決めた回数を数える（KB ごとに 1 回だけ。#5499）。"""
        self.resolved.append(dict(filters))
        return dict(filters)

    async def pages(self, scope: dict[str, str], **kwargs: Any) -> Any:
        if scope.get("document_id") == "doc-a":
            return self.a
        if kwargs.get("unit_ids") is not None:
            return [p for p in self.b if p.unit.unit_id in kwargs["unit_ids"]]
        assert kwargs["exclude_document_ids"] == ["doc-a"]
        return self.b[: kwargs["limit"]]

    async def text_rankings(self, text: str, scope: Any, **kwargs: Any) -> Any:
        assert kwargs["exclude_document_ids"] == ["doc-a"]
        return [(p.unit.document_id, p.unit.unit_id) for p in self.b]


@pytest.fixture
def link_setup(monkeypatch: pytest.MonkeyPatch) -> Any:
    store, model = LinkStore(), LinkModel()
    settings = Settings(rag_wiki_link_batch_units=8)
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(module, "compiler_fingerprint", lambda settings: "compiler-a")
    monkeypatch.setattr(module, "enterprise_ai_default_model_id", lambda settings: "model-a")
    embedded: list[list[str]] = []

    async def actor(owner: Any, settings: Any) -> Any:
        return AuditRequestContext(user_uuid=owner)

    class Embedder:
        async def embed(self, texts: Any, **kwargs: Any) -> Any:
            assert kwargs == {"input_type": "SEARCH_QUERY"}
            embedded.append(list(texts))
            return [[1.0, 0.0] for _ in texts]

    jobs = module.WikiJobs(
        store_factory=lambda: store,
        actor=actor,
        model_factory=lambda settings: model,
        embedder_factory=lambda settings: Embedder(),
    )
    return jobs, store, model, settings, embedded


@pytest.mark.asyncio
async def test_links_are_built_both_ways_from_candidates_once(link_setup: Any) -> None:
    jobs, store, model, settings, embedded = link_setup
    await jobs._build_links("doc-a", "owner-a", settings)
    assert store.state["status"] == "READY"
    # 前向き（A の 2 単位 → B）と逆向き（B の単位 → A）を、候補の組だけで 2 回で判定する。
    assert len(model.calls) == 2 and len(embedded) == 1
    forward, reverse = store.saved
    assert forward["sources"] == ["doc-a-a1", "doc-a-a2"]
    assert forward["candidates"] == ["doc-b-b1"]
    assert reverse["sources"] == ["doc-b-b1"] and reverse["candidates"] == ["doc-a-a1", "doc-a-a2"]
    assert reverse["links"] == [("doc-b-b1", "doc-a-a1", "exception", "合成")]
    # 範囲は自分の文書と KB ごとに 1 回だけ決め、起点の単位（2 つ）ごとには決め直さない（#5499）。
    assert [scope.get("document_id") for scope in store.resolved] == ["doc-a", None]
    # 同じ原文・同じ条件なら作り直さない。
    await jobs._build_links("doc-a", "owner-a", settings)
    assert len(model.calls) == 2
    # 所属する KB が変われば候補の範囲が変わるので作り直す。
    store.kbs = ["kb-a", "kb-b"]
    await jobs._build_links("doc-a", "owner-a", settings)
    assert len(model.calls) == 4
    await jobs.shutdown()


@pytest.mark.asyncio
async def test_link_failure_keeps_the_document_ready_and_waits_for_retry(link_setup: Any) -> None:
    jobs, store, _, settings, _ = link_setup
    failing = LinkModel(fail=True)
    jobs.model_factory = lambda settings: failing
    await jobs._build_links("doc-a", "owner-a", settings)
    assert store.state["status"] == "FAILED" and store.state["failure_code"] == "build_failed"
    assert store.doc["status"] == "READY"
    # 失敗は再試行の API が待機へ戻すまで作り直さない。
    await jobs._build_links("doc-a", "owner-a", settings)
    assert len(failing.calls) == 1
    await jobs.shutdown()


@pytest.mark.asyncio
async def test_interrupted_link_build_resumes_from_saved_keys(link_setup: Any) -> None:
    jobs, store, model, settings, _ = link_setup
    store.state = {
        "source_fingerprint": "source-a",
        "link_fingerprint": module.link_fingerprint(settings, "compiler-a", ["kb-a"]),
        "status": "FAILED",
        "failure_code": "interrupted",
        "leased": 0,
        "done": ["forward:doc-a-a1", "forward:doc-a-a2"],
    }
    await jobs._build_links("doc-a", "owner-a", settings)
    # 判定済みの前向きは飛ばし、逆向きだけを判定する。
    assert len(model.calls) == 1 and model.calls[0]["sources"][0]["unit_id"] == "S1"
    assert [entry["sources"] for entry in store.saved] == [["doc-b-b1"]]
    assert store.state["status"] == "READY"
    await jobs.shutdown()


@pytest.mark.asyncio
async def test_unpublished_document_or_zero_candidates_builds_no_links(
    link_setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs, store, model, settings, _ = link_setup
    store.doc["status"] = "RUNNING"
    await jobs._build_links("doc-a", "owner-a", settings)
    store.doc["status"] = "READY"
    await jobs._build_links(
        "doc-a", "owner-a", settings.model_copy(update={"rag_wiki_link_candidates": 0})
    )
    assert store.state is None and not model.calls
    await jobs.shutdown()


@pytest.mark.asyncio
async def test_oversized_input_is_split_before_calling_the_model() -> None:
    items = [
        WikiLinkItem(f"forward:{name}", page("doc-a", name), (page("doc-b", "b1"),))
        for name in ("a1", "a2")
    ]
    model = LinkModel()
    calls = await judge_links(
        items,
        model=model,
        batch_size=2,
        original_chars=100,
        max_input_chars=400,
        authorize=allow,
        checkpoint=lambda *args: allow(),
    )
    assert calls == 2 and [len(call["sources"]) for call in model.calls] == [1, 1]
