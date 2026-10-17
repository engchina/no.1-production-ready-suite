"""条の目録から質問の根拠になる条を選び、その条の根拠を候補に加えて起点の枠を確保する (#2254)。"""

import json
from dataclasses import replace

from rag_engine.config import get_settings
from rag_engine.generation import answering
from rag_engine.retrieval.article_catalog import (
    ARTICLE_CATALOG_KEY,
    ARTICLE_SELECTION_SYSTEM_PROMPT,
    ArticleCatalog,
    ArticleSelection,
    interleave_selections,
    is_catalog_article,
    select_articles,
)
from tests.test_document_selection import child

CATALOG = ArticleCatalog(
    document_id="doc-a",
    file_name="架空規程.json",
    chunk_set_id="cs-a",
    lines=("架空規程", "  第一章 総則", "    第一条（目的）", "    第二条（届出）", "    第三条｜前条の届出は、"),
    section_ids=("s0", "s1", "s2", "s3", "s4"),
)


def _parse(lines):
    def parse(system, prompt, settings, schema, **kwargs):
        assert system == ARTICLE_SELECTION_SYSTEM_PROMPT
        assert schema is ArticleSelection
        parse.prompt = json.loads(prompt)
        return ArticleSelection(lines=lines)

    return parse


def test_select_articles_keeps_only_catalog_lines_in_order_up_to_the_limit():
    parse = _parse([3, 9, 3, -1, 4, 2])

    chosen = select_articles("届出の期限は", CATALOG, parse, None, limit=2)

    # 目録に無い番号・重複は捨て、上限で切る。行は字下げを除いて返す。
    assert chosen == [("s3", "第二条（届出）"), ("s4", "第三条｜前条の届出は、")]
    assert parse.prompt["max_lines"] == 2
    assert parse.prompt["document"] == "架空規程.json"
    assert "[3]     第二条（届出）" in parse.prompt["catalog"]


def test_select_articles_returns_empty_when_the_model_fails():
    def failing(*args, **kwargs):
        raise RuntimeError("timeout")

    assert select_articles("届出の期限は", CATALOG, failing, None, limit=3) == []
    assert select_articles("届出の期限は", replace(CATALOG, lines=(), section_ids=()), _parse([0]), None, limit=3) == []


def test_interleave_takes_the_first_choice_of_each_document_first():
    other = replace(CATALOG, document_id="doc-b", file_name="架空細則.json", chunk_set_id="cs-b")
    ordered = interleave_selections([CATALOG, other], [[("s2", "a1"), ("s3", "a2")], [("t1", "b1")]])

    assert [(a.document_id, a.section_id, a.order) for a in ordered] == [("doc-a", "s2", 0), ("doc-b", "t1", 1), ("doc-a", "s3", 2)]


def _marked(record, order=0, *, added=True):
    info = {"order": order, "document": "架空規程.json", "title": "t", "added": added}
    return replace(record, metadata={**record.metadata, ARTICLE_CATALOG_KEY: info})


def test_catalog_articles_are_not_deferred_by_document_selection():
    settings = replace(get_settings(environ={}, dotenv_path=None), document_selection_enabled=True)
    top = child("m1", 1, "手引き", "架空手引き.pdf", "p1", score=0.9)
    article = _marked(child("art1", 2, "第一条", "架空規程.json", "q1", score=0.2))

    kept, deferred, _trace = answering.select_documents([top, article], settings)

    assert [r.id for r in kept] == ["m1", "art1"]
    assert deferred == []


def test_engine_settings_default_and_env():
    defaults = get_settings(environ={}, dotenv_path=None)
    assert defaults.article_catalog_enabled is True
    assert (defaults.article_catalog_max_documents, defaults.article_catalog_max_articles) == (3, 8)
    assert defaults.article_catalog_context_records == 6
    environ = {"RAG_ENGINE_ARTICLE_CATALOG": "0", "RAG_ENGINE_ARTICLE_CATALOG_CONTEXT_RECORDS": "2"}
    configured = get_settings(environ=environ, dotenv_path=None)
    assert configured.article_catalog_enabled is False
    assert configured.article_catalog_context_records == 2


def _stored(uid, source, *, level="child", article=None, document_id="doc-a"):
    from rag_engine.models.storage import StoredChunk

    metadata = {"document_id": document_id}
    if article is not None:
        metadata[ARTICLE_CATALOG_KEY] = article
    return StoredChunk(
        chunk_uid=uid,
        chunk_id=uid,
        chunk_level=level,
        chunk_seq=1,
        parent_chunk_uid="" if level == "parent" else f"{uid}:p",
        parent_chunk_id="",
        child_chunk_ids=(),
        text=uid,
        retrieval_text=uid,
        source_run_id="run",
        source_file_name=source,
        source_engine_id="docling",
        source_engine_label="Docling",
        page_start=1,
        page_end=1,
        source_seq_ranges=(),
        source_record_refs=(),
        metadata=metadata,
    )


def test_catalog_article_children_reads_catalogs_and_chunks_from_injected_dependencies():
    """候補の文書の目録から条を選び、候補に無い条は加え、候補にある条は印を付け、親を展開用に返す。"""
    from rag_engine.dependencies import AnswerDependencies, bind_dependencies

    calls = []

    def unused(*args, **kwargs):
        raise AssertionError("呼ばない")

    def article_catalogs(*, document_ids, limit, settings):
        calls.append(("catalogs", list(document_ids), limit))
        return [CATALOG]

    def article_chunks(*, articles, settings):
        calls.append(("chunks", [a.section_id for a in articles]))
        info = {a.section_id: {"order": a.order, "document": a.file_name, "title": a.title} for a in articles}
        return [
            _stored("hit", "架空規程.json", article=info["s2"]),
            _stored("new", "架空規程.json", article=info["s3"]),
            _stored("new:p", "架空規程.json", level="parent"),
        ]

    dependencies = AnswerDependencies(
        search=unused,
        check_ready=unused,
        rerank=unused,
        parse_images=unused,
        parse_text=lambda system, prompt, settings, schema, **kwargs: ArticleSelection(lines=[2, 3]),
        article_catalogs=article_catalogs,
        article_chunks=article_chunks,
    )
    pairs = (("m1", "架空手引き.pdf"), ("hit", "架空規程.json"))
    ranked = [answering._stored_chunk_answer_record(_stored(uid, name)) for uid, name in pairs]
    ranked[0].metadata["document_id"] = "doc-m"
    settings = get_settings(environ={}, dotenv_path=None)
    answering._ARTICLE_SELECTION_CACHE.clear()

    with bind_dependencies(dependencies):
        children, expansion = answering._catalog_article_children("届出の期限は", ranked, settings)
        # 同じ質問・文書は選び直さない（CRAG の各回）。
        answering._catalog_article_children("届出の期限は", ranked, settings)

    assert [r.chunk_uid for r in children] == ["m1", "hit", "new"]
    assert [is_catalog_article(r) for r in children] == [False, True, True]
    # 候補にあった条は印だけ（検索の根拠として扱う）、加えた条は added。
    assert [r.metadata.get(ARTICLE_CATALOG_KEY, {}).get("added") for r in children] == [None, None, True]
    assert [r.chunk_uid for r in expansion] == ["hit", "new", "new:p"]
    assert calls[0] == ("catalogs", ["doc-m", "doc-a"], 3)
    assert calls[1] == ("chunks", ["s2", "s3"])
    assert [c[0] for c in calls].count("catalogs") == 2  # 目録は呼び出し元が回答ごとに持つ


def test_catalog_article_children_without_dependencies_keeps_the_candidates():
    ranked = [answering._stored_chunk_answer_record(_stored("m1", "架空手引き.pdf"))]
    settings = get_settings(environ={}, dotenv_path=None)

    children, expansion = answering._catalog_article_children("届出の期限は", ranked, settings)

    assert children == ranked
    assert expansion == []


def _context_record(name, level, *, article=False, added=True):
    from rag_engine.generation.answering import AnswerRecord

    metadata = {"section_path": ["第 1 章"]}
    if article:
        metadata[ARTICLE_CATALOG_KEY] = {"order": 0, "document": "架空規程.json", "title": name, "added": added}
    return AnswerRecord(
        id=name if level == "child" else f"{name}-p",
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="Chunk",
        text=name,
        source="架空規程.json" if name.startswith("art") else "架空手引き.pdf",
        chunk_level=level,
        chunk_id=name if level == "child" else f"{name}-p",
        chunk_uid=f"uid:{name}" if level == "child" else f"uid:{name}-p",
        parent_chunk_uid=f"uid:{name}-p" if level == "child" else "",
        source_run_id="run",
        metadata=metadata,
    )


def _bundle(children, *, top_k, max_records, catalog_records):
    from rag_engine.retrieval.context_builder import ContextBuildRequest, build_chunk_context_bundle

    names = [r.id for r in children]
    parents = [_context_record(name, "parent") for name in names]
    return build_chunk_context_bundle(
        ContextBuildRequest(
            question="質問",
            ranked_children=children,
            active_records=[*children, *parents],
            top_k=top_k,
            neighbor_child_count=0,
            max_records=max_records,
            max_chars=5000,
            catalog_records=catalog_records,
        )
    )


def test_context_adds_catalog_articles_outside_the_anchor_and_parent_limits():
    """目録で選んだ条は起点（top_k）にも親の枠（max_records）にも数えず、外に足す（検索の根拠を減らさない）。"""
    children = [_context_record("art1", "child", article=True)]
    children += [_context_record(f"m{index}", "child") for index in range(5)]
    children += [_context_record("art2", "child", article=True), _context_record("art3", "child", article=True)]

    bundle = _bundle(children, top_k=3, max_records=3, catalog_records=2)
    # 検索の根拠の親は目録の条が無いときと同じ 3 つ。選んだ条は関連度の順に 2 つまで後ろへ足す。
    assert [r.id for r in bundle.records] == ["m0-p", "m1-p", "m2-p", "art1-p", "art2-p"]

    plain = _bundle(children, top_k=3, max_records=3, catalog_records=0)
    assert [r.id for r in plain.records] == ["art1-p", "m0-p", "m1-p"]


def test_catalog_articles_are_not_counted_by_the_per_document_anchor_cap():
    """文書ごとの起点の上限（#2167）は目録で選んだ条を数えず、位置を保つ（選んだ条の枠で並べる）。"""
    settings = replace(get_settings(environ={}, dotenv_path=None), max_anchors_per_document=1)
    records = [
        child("a1", 1, "第一条", "架空規程.json", "q1"),
        child("a2", 2, "第二条", "架空規程.json", "q2"),
        _marked(child("a3", 3, "第三条", "架空規程.json", "q3")),
        child("m1", 4, "手引き", "架空手引き.pdf", "p1"),
    ]

    capped = answering.cap_anchors_per_document(records, settings, top_k=3)

    assert [r.id for r in capped] == ["a1", "a3", "m1", "a2"]


def test_select_documents_keeps_listed_documents_in_order():
    from rag_engine.retrieval.article_catalog import ARTICLE_DOCUMENT_SELECTION_SYSTEM_PROMPT, select_documents

    documents = [("doc-a", "架空規程.json"), ("doc-b", "架空手続通則.json"), ("doc-c", "架空細則.json")]

    def parse(system, prompt, settings, schema, **kwargs):
        assert system == ARTICLE_DOCUMENT_SELECTION_SYSTEM_PROMPT
        assert "[1] 架空手続通則.json" in json.loads(prompt)["documents"]
        return ArticleSelection(lines=[1, 7, 1, 0, 2])

    assert select_documents("届出の補正は", documents, parse, None, limit=2) == ["doc-b", "doc-a"]
    assert select_documents("届出の補正は", [], parse, None, limit=2) == []


def test_catalog_article_children_adds_documents_chosen_from_the_scope_list():
    """候補に出ない文書も、範囲の文書の一覧から LLM が選べば目録を読む（候補の文書の後ろ）。"""
    from rag_engine.dependencies import AnswerDependencies, bind_dependencies
    from rag_engine.retrieval.article_catalog import ArticleSelection as Selection

    calls = []
    general = replace(CATALOG, document_id="doc-g", file_name="架空手続通則.json", chunk_set_id="cs-g")

    def article_catalogs(*, document_ids, limit, settings):
        calls.append(("catalogs", list(document_ids), limit))
        return [CATALOG] if "doc-a" in document_ids else [general]

    def parse(system, prompt, settings, schema, **kwargs):
        body = json.loads(prompt)
        return Selection(lines=[1, 0]) if "documents" in body else Selection(lines=[2])

    def unused(*args, **kwargs):
        raise AssertionError("呼ばない")

    dependencies = AnswerDependencies(
        search=unused,
        check_ready=unused,
        rerank=unused,
        parse_images=unused,
        parse_text=parse,
        article_catalogs=article_catalogs,
        article_chunks=lambda *, articles, settings: calls.append(("chunks", [(a.document_id, a.section_id) for a in articles])) or [],
        article_documents=lambda *, settings: [("doc-a", "架空規程.json"), ("doc-g", "架空手続通則.json")],
    )
    ranked = [answering._stored_chunk_answer_record(_stored("hit", "架空規程.json"))]
    settings = get_settings(environ={}, dotenv_path=None)
    answering._ARTICLE_SELECTION_CACHE.clear()
    answering._ARTICLE_DOCUMENT_CACHE.clear()

    with bind_dependencies(dependencies):
        answering._catalog_article_children("届出の補正は", ranked, settings)

    # 一覧から選んだ doc-g・doc-a のうち、候補の文書（doc-a）に無い doc-g だけを足して読む。
    assert calls[0] == ("catalogs", ["doc-a"], 3)
    assert calls[1] == ("catalogs", ["doc-g"], 1)
    assert calls[2] == ("chunks", [("doc-a", "s2"), ("doc-g", "s2")])


def test_catalog_articles_do_not_raise_the_top_document_score():
    """目録で選んだ条の関連度が高くても、他の文書を後回しにする基準（最上位の文書の分数）に数えない。"""
    settings = replace(get_settings(environ={}, dotenv_path=None), document_selection_enabled=True)
    manual = child("m1", 1, "手引き", "架空手引き.pdf", "p1", score=0.4)
    article = _marked(child("art1", 2, "第一条", "架空規程.json", "q1", score=0.95))

    kept, deferred, trace = answering.select_documents([manual, article], settings)

    assert [r.id for r in kept] == ["m1", "art1"]
    assert deferred == []
    assert [entry["source"] for entry in trace["selected"]] == ["架空手引き.pdf"]


def test_catalog_article_sharing_a_parent_does_not_change_the_hit_parents():
    """選んだ条が検索の根拠と同じ親にあっても、検索の根拠の親の選び方（順位・数）は選んだ条が無いときと同じ。"""
    shared = replace(_context_record("art1", "child", article=True), parent_chunk_uid="uid:m2-p")
    children = [_context_record(f"m{index}", "child") for index in range(4)]
    with_article = [shared, *children]

    bundle = _bundle(with_article, top_k=4, max_records=2, catalog_records=2)
    plain = _bundle(children, top_k=4, max_records=2, catalog_records=2)

    assert [r.id for r in plain.records] == ["m0-p", "m1-p"]
    # m2 の親は枠の外だったので、選んだ条の親として外に足す。
    assert [r.id for r in bundle.records] == ["m0-p", "m1-p", "m2-p"]


def test_catalog_article_already_in_the_candidates_stays_a_hit_anchor():
    """検索の候補にあった条は、印が付いても検索の根拠として起点になる（検索の根拠の選び方を変えない）。"""
    existing = _context_record("art1", "child", article=True, added=False)
    children = [existing, *[_context_record(f"m{index}", "child") for index in range(3)]]

    bundle = _bundle(children, top_k=2, max_records=2, catalog_records=2)
    plain = _bundle([_context_record("art1", "child"), *children[1:]], top_k=2, max_records=2, catalog_records=0)

    assert [r.id for r in bundle.records] == [r.id for r in plain.records] == ["art1-p", "m0-p"]
