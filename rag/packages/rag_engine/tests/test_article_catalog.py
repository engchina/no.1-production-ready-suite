"""条の目録から質問の根拠になる条を選び、その条の根拠を候補に加えて起点の枠を確保する (#2254)。"""

import json
from dataclasses import replace

from rag_engine.config import get_settings
from rag_engine.generation import answering
from rag_engine.retrieval.article_catalog import (
    ARTICLE_CATALOG_KEY,
    ARTICLE_SELECTION_SYSTEM_PROMPT,
    ArticleCatalog,
    ArticleIssue,
    ArticleIssueSelection,
    ArticleSelection,
    CatalogTerm,
    interleave_selections,
    is_catalog_article,
    select_articles,
)
from tests.test_document_selection import child


def _articles(lines, *more):
    """条の選択の出力（論点ごと）。`more` は 2 つ目以降の論点の行。"""
    return ArticleIssueSelection(issues=[ArticleIssue(issue=f"論点{i}", lines=list(group)) for i, group in enumerate((lines, *more), 1)])


def _picked(schema, lines):
    """呼ばれた schema に合わせて、文書の選択・条の選択の出力を返す。"""
    return _articles(lines) if schema is ArticleIssueSelection else ArticleSelection(lines=lines)


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
        assert schema is ArticleIssueSelection
        parse.prompt = json.loads(prompt)
        return _articles(lines)

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
    assert (defaults.article_catalog_max_documents, defaults.article_catalog_max_articles) == (8, 12)
    assert defaults.article_catalog_context_records == 6
    assert defaults.article_catalog_original_question_search is True
    environ = {
        "RAG_ENGINE_ARTICLE_CATALOG": "0",
        "RAG_ENGINE_ARTICLE_CATALOG_CONTEXT_RECORDS": "2",
        "RAG_ENGINE_ARTICLE_CATALOG_ORIGINAL_QUESTION_SEARCH": "0",
    }
    configured = get_settings(environ=environ, dotenv_path=None)
    assert configured.article_catalog_enabled is False
    assert configured.article_catalog_context_records == 2
    assert configured.article_catalog_original_question_search is False


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
        parse_text=lambda system, prompt, settings, schema, **kwargs: _articles([2, 3]),
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
    assert calls[0] == ("catalogs", ["doc-m", "doc-a"], 8)
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
    # 選んだ条のために足した親は理由で分かる（MCP は根拠の上限に数えない。#2261）。
    assert [item.reason for item in bundle.evidence_tree][-2:] == ["catalog_article"] * 2
    assert "catalog_article" not in [item.reason for item in bundle.evidence_tree][:3]

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

    asked = []

    def parse(system, prompt, settings, schema, **kwargs):
        body = json.loads(prompt)
        if "documents" in body:
            asked.append(body["max_lines"])
            return Selection(lines=[1, 0])
        return _articles([2])

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
    assert calls[0] == ("catalogs", ["doc-a"], 8)
    assert calls[1] == ("catalogs", ["doc-g"], 1)
    # 候補の文書と重なる分（doc-a の 1 つ）だけ多く頼む（既定の 2 + 1）。
    assert asked == [3]
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


def test_catalog_documents_are_ordered_by_the_sum_of_reciprocal_ranks():
    """1 つの chunk が上位に当たった文書より、多くの chunk が当たった文書を先にする（#2261）。"""

    def record(uid, document_id):
        stored = _stored(uid, f"{document_id}.json", document_id=document_id)
        return answering._stored_chunk_answer_record(stored)

    ranked = [record("a1", "doc-a"), *[record(f"b{i}", "doc-b") for i in range(4)], record("c1", "doc-c")]

    assert answering._catalog_document_ids(ranked) == ["doc-b", "doc-a", "doc-c"]


def test_catalog_selection_uses_the_original_question_with_the_query():
    """呼び出し元が元の質問を渡したら、条の選択には元の質問と検索文を渡す（検索は検索文のまま。#2261）。"""
    from rag_engine.dependencies import AnswerDependencies, bind_dependencies

    prompts = []

    def unused(*args, **kwargs):
        raise AssertionError("呼ばない")

    def parse(system, prompt, settings, schema, **kwargs):
        prompts.append(json.loads(prompt)["question"])
        return _picked(schema, [])

    def dependencies(original):
        return AnswerDependencies(
            search=unused,
            check_ready=unused,
            rerank=unused,
            parse_images=unused,
            parse_text=parse,
            article_catalogs=lambda *, document_ids, limit, settings: [CATALOG],
            article_chunks=lambda *, articles, settings: [],
            article_question=lambda: original,
        )

    ranked = [answering._stored_chunk_answer_record(_stored("hit", "架空規程.json"))]
    settings = get_settings(environ={}, dotenv_path=None)
    answering._ARTICLE_SELECTION_CACHE.clear()

    with bind_dependencies(dependencies("取引先から届いた書類の補正を求めるべきか")):
        answering._catalog_article_children("書類 表記", ranked, settings)
    with bind_dependencies(dependencies(None)):
        answering._catalog_article_children("書類 表記", ranked, settings)

    assert prompts[0] == "取引先から届いた書類の補正を求めるべきか\n\n（検索文: 書類 表記）"
    assert prompts[-1] == "書類 表記"


def test_catalog_documents_are_ranked_with_the_original_question_candidates():
    """元の質問の候補は文書の順位に足す（列ごとの順位の逆数の合計）。検索文の候補に出ない文書も並ぶ。"""
    query_hits = [answering._stored_chunk_answer_record(_stored(f"q{i}", "架空手引き.pdf", document_id="manual")) for i in range(3)]
    original_hits = [
        answering._stored_chunk_answer_record(_stored("o1", "架空政令.json", document_id="order")),
        answering._stored_chunk_answer_record(_stored("o2", "架空政令.json", document_id="order")),
        answering._stored_chunk_answer_record(_stored("o3", "架空手引き.pdf", document_id="manual")),
    ]

    assert answering._catalog_document_ids(query_hits) == ["manual"]
    assert answering._catalog_document_ids(query_hits, original_hits) == ["manual", "order"]
    # 元の質問の候補で多く当たった文書は、検索文の候補の 1 件だけの文書より前に来る。
    assert answering._catalog_document_ids(query_hits[:1], original_hits) == ["order", "manual"]


def test_original_question_search_runs_once_vector_only_and_only_when_injected(monkeypatch):
    """元の質問が注入され検索文と違うときだけ、元の質問をベクトルだけで 1 回検索する。失敗・設定 off は空。"""
    from types import SimpleNamespace

    from rag_engine.dependencies import AnswerDependencies, bind_dependencies

    calls = []

    def search(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(child_chunks=[_stored("o1", "架空政令.json", document_id="order")])

    monkeypatch.setattr(answering, "search_adb_hybrid_chunks", search)
    settings = get_settings(environ={}, dotenv_path=None)
    options = {"chunk_run_id": "", "retrieval_queries": ["書類 表記"], "settings": settings, "extra_text_queries": ("x",)}

    def deps(original):
        def unused(*args, **kwargs):
            raise AssertionError("呼ばない")

        return AnswerDependencies(
            search=unused,
            check_ready=unused,
            rerank=unused,
            parse_images=unused,
            parse_text=unused,
            article_question=lambda: original,
        )

    with bind_dependencies(deps("取引先から届いた書類の補正を求めるべきか")):
        found = answering._original_question_children("書類 表記", ["書類 表記"], dict(options), None, settings)
    assert [record.chunk_uid for record in found] == ["o1"]
    assert calls[0]["retrieval_queries"] == ["取引先から届いた書類の補正を求めるべきか"]
    assert calls[0]["vector_only_queries"] == ("取引先から届いた書類の補正を求めるべきか",)
    assert "extra_text_queries" not in calls[0]

    with bind_dependencies(deps(None)):
        assert answering._original_question_children("書類 表記", ["書類 表記"], dict(options), None, settings) == []
    with bind_dependencies(deps("書類 表記")):
        assert answering._original_question_children("書類 表記", ["書類 表記"], dict(options), None, settings) == []
    off = replace(settings, article_catalog_original_question_search=False)
    with bind_dependencies(deps("取引先から届いた書類の補正を求めるべきか")):
        assert answering._original_question_children("書類 表記", ["書類 表記"], dict(options), None, off) == []
    assert len(calls) == 1

    def broken(**kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr(answering, "search_adb_hybrid_chunks", broken)
    with bind_dependencies(deps("取引先から届いた書類の補正を求めるべきか")):
        assert answering._original_question_children("書類 表記", ["書類 表記"], dict(options), None, settings) == []


def _select_with(parse, settings, catalogs, terms=(), leads=None, refined=None):
    from rag_engine.dependencies import AnswerDependencies, bind_dependencies

    def unused(*args, **kwargs):
        raise AssertionError("呼ばない")

    dependencies = AnswerDependencies(
        search=unused, check_ready=unused, rerank=unused, parse_images=unused, parse_text=parse, article_leads=leads
    )
    with bind_dependencies(dependencies):
        selected, failed, added = answering._select_catalog_articles("届出の期限は", catalogs, settings, terms=terms)
    if refined is not None:
        refined.append(added)
    return selected, failed


def test_runaway_selection_skips_only_that_document_and_is_not_cached():
    """出力の上限で終わらない文書だけ選ばずに続け、工程に残す。失敗は記録しない（次は呼び直す）。"""
    from rag_engine.adapters import oci

    other = replace(CATALOG, document_id="doc-b", file_name="架空細則.json", chunk_set_id="cs-b")
    caps = []

    def parse(system, prompt, settings, schema, **kwargs):
        caps.append(oci._output_cap.get())
        if json.loads(prompt)["document"] == other.file_name:
            raise oci.IncompleteResponse("未完了", "max_output_tokens")
        return _articles([2])

    settings = replace(get_settings(environ={}, dotenv_path=None), article_catalog_output_token_cap=512)
    answering._ARTICLE_SELECTION_CACHE.clear()

    selected, failed = _select_with(parse, settings, [CATALOG, other])
    _select_with(parse, settings, [CATALOG, other])

    assert [a.section_id for a in selected] == ["s2"]
    assert failed == [("架空細則.json", "出力の上限")]
    # 呼び出しごとに役目の上限を掛ける。成功した文書だけ記録し、失敗した文書は 2 回目も呼ぶ。
    assert caps == [512, 512, 512]


def test_slow_selection_is_cut_by_the_per_call_time_limit():
    """1 回の時間の上限を超えた文書は選ばずに続け、外側の期限は切らない。"""
    import time

    from rag_engine.resources.deadline import Deadline, bind_deadline, check_deadline

    other = replace(CATALOG, document_id="doc-b", file_name="架空細則.json", chunk_set_id="cs-b")

    def parse(system, prompt, settings, schema, **kwargs):
        if json.loads(prompt)["document"] == other.file_name:
            while True:
                check_deadline()
                time.sleep(0.01)
        return _articles([3])

    settings = replace(get_settings(environ={}, dotenv_path=None), article_catalog_call_timeout_seconds=0.1)
    answering._ARTICLE_SELECTION_CACHE.clear()
    outer = Deadline(60)

    started = time.monotonic()
    with bind_deadline(outer):
        selected, failed = _select_with(parse, settings, [CATALOG, other])

    assert time.monotonic() - started < 5
    assert [a.section_id for a in selected] == ["s3"]
    assert failed == [("架空細則.json", "時間の上限")]
    assert not outer.expired


def test_article_catalog_call_limits_come_from_the_environment():
    defaults = get_settings(environ={}, dotenv_path=None)
    custom = get_settings(
        environ={"RAG_ENGINE_ARTICLE_CATALOG_OUTPUT_TOKEN_CAP": "1024", "RAG_ENGINE_ARTICLE_CATALOG_CALL_TIMEOUT_SECONDS": "20"},
        dotenv_path=None,
    )
    assert (defaults.article_catalog_output_token_cap, defaults.article_catalog_call_timeout_seconds) == (4096, 90.0)
    assert (custom.article_catalog_output_token_cap, custom.article_catalog_call_timeout_seconds) == (1024, 20.0)


def test_select_articles_takes_each_issue_in_turn_and_passes_terms():
    """論点ごとの条は、各論点の 1 番目から順に回して上限で切る。登録の用語は別名・説明つきで渡す。"""
    parse = _parse([2, 3])

    def parse_issues(system, prompt, settings, schema, **kwargs):
        parse_issues.prompt = json.loads(prompt)
        return _articles([2, 3], [4])

    terms = (CatalogTerm(term="架空の通称", aliases=("届出の特例",), description="規程の届出の特例。"),)

    chosen = select_articles("通称の手続は", CATALOG, parse_issues, None, limit=2, terms=terms)

    # 1 つ目の論点の 2 番目より、2 つ目の論点の 1 番目を先に取る。
    assert chosen == [("s2", "第一条（目的）"), ("s4", "第三条｜前条の届出は、")]
    assert parse_issues.prompt["terms"] == [{"term": "架空の通称", "aliases": ["届出の特例"], "description": "規程の届出の特例。"}]
    # 用語が無ければ terms を渡さない。
    select_articles("届出の期限は", CATALOG, parse, None, limit=2)
    assert "terms" not in parse.prompt


def test_catalog_selection_passes_matched_registered_terms(tmp_path):
    """質問に一致した登録の用語（実行時の知識）を条の選択に渡し、工程に残す。"""
    knowledge = tmp_path / "runtime_knowledge.json"
    knowledge.write_text(
        json.dumps(
            {"schema_version": 1, "terms": [{"term": "架空の通称", "aliases": ["届出の特例"], "status": "approved"}]}, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    seen = []

    def parse(system, prompt, settings, schema, **kwargs):
        seen.append(json.loads(prompt).get("terms"))
        return _articles([2])

    settings = replace(get_settings(environ={}, dotenv_path=None), runtime_knowledge_path=str(knowledge), output_dir=str(tmp_path))
    answering._ARTICLE_SELECTION_CACHE.clear()

    selected, failed = _select_with(parse, settings, [CATALOG], terms=answering._catalog_terms("架空の通称の届出は", settings))

    assert [a.section_id for a in selected] == ["s2"] and failed == []
    assert seen == [[{"term": "架空の通称", "aliases": ["届出の特例"]}]]


NESTED = ArticleCatalog(
    document_id="doc-n",
    file_name="架空法.json",
    chunk_set_id="cs-n",
    lines=(
        "第一章 総則",
        "  第一条（目的）",
        "  第二条（定義）",
        "第二章 特例",
        "  第三条（特例の対象）",
        "  第四条（記録）",
        "  第五条（請求を行う場合の特例）",
        "第三章 雑則",
        "  第六条（委任）",
    ),
    section_ids=tuple(f"n{i}" for i in range(9)),
)


def test_chapter_members_are_the_other_articles_under_the_same_heading():
    from rag_engine.retrieval.article_catalog import chapter_members

    assert chapter_members(NESTED, [4], limit=10) == [5, 6]
    assert chapter_members(NESTED, [4, 1], limit=10) == [2, 5, 6]
    assert chapter_members(NESTED, [4], limit=1) == [5]
    # 見出し（最上位の行）だけを選んだときは同じ章を数えない。
    assert chapter_members(NESTED, [3], limit=10) == []


def test_refine_adds_articles_of_the_same_chapter_judged_by_their_leads():
    """2 段目は選んだ条と同じ章の条を書き出しつきで見せ、足した条だけを返す（候補に無い番号・選んだ条は捨てる）。"""
    from rag_engine.retrieval.article_catalog import ARTICLE_REFINE_SYSTEM_PROMPT, refine_articles

    asked = {}

    def leads(sections):
        asked["sections"] = dict(sections)
        return {"n6": "前条の請求があつたときは、特例の請求があつたものとみなす。"}

    def parse(system, prompt, settings, schema, **kwargs):
        assert system == ARTICLE_REFINE_SYSTEM_PROMPT and schema is ArticleIssueSelection
        asked["prompt"] = json.loads(prompt)
        return _articles([6, 4, 0], [5])

    added = refine_articles("重なったときは", NESTED, [("n4", "第三条（特例の対象）")], leads, parse, None, limit=2, candidate_limit=10)

    assert added == [("n6", "第五条（請求を行う場合の特例）"), ("n5", "第四条（記録）")]
    assert asked["sections"] == {"n5": "第四条（記録）", "n6": "第五条（請求を行う場合の特例）"}
    assert asked["prompt"]["selected"] == ["第三条（特例の対象）"]
    assert "[6] 第五条（請求を行う場合の特例）｜前条の請求があつたときは" in asked["prompt"]["candidates"]
    assert "[5] 第四条（記録）" in asked["prompt"]["candidates"]


def test_catalog_selection_runs_the_second_stage_when_leads_are_injected():
    """書き出しの読み込みが注入されたときだけ 2 段目を行い、足した条を文書の選択の後ろに置いて数える。"""
    prompts = []

    def parse(system, prompt, settings, schema, **kwargs):
        body = json.loads(prompt)
        prompts.append("candidates" in body)
        return _articles([6]) if "candidates" in body else _articles([4])

    def leads(*, document_id, chunk_set_id, sections, settings):
        assert (document_id, chunk_set_id) == ("doc-n", "cs-n")
        return {}

    settings = get_settings(environ={}, dotenv_path=None)
    refined = []
    answering._ARTICLE_SELECTION_CACHE.clear()
    selected, failed = _select_with(parse, settings, [NESTED], leads=leads, refined=refined)
    assert [a.section_id for a in selected] == ["n4", "n6"] and failed == [] and refined == [1] and prompts == [False, True]

    answering._ARTICLE_SELECTION_CACHE.clear()
    prompts.clear()
    selected, _ = _select_with(parse, settings, [NESTED])
    assert [a.section_id for a in selected] == ["n4"] and prompts == [False]

    # 2 段目が失敗しても 1 段目の条で続ける。
    def broken(*, document_id, chunk_set_id, sections, settings):
        raise RuntimeError("db")

    answering._ARTICLE_SELECTION_CACHE.clear()
    selected, failed = _select_with(parse, settings, [NESTED], leads=broken)
    assert [a.section_id for a in selected] == ["n4"] and failed == []


def test_article_catalog_refine_settings_come_from_the_environment():
    defaults = get_settings(environ={}, dotenv_path=None)
    off = get_settings(environ={"RAG_ENGINE_ARTICLE_CATALOG_REFINE_ARTICLES": "0"}, dotenv_path=None)
    assert (defaults.article_catalog_refine_articles, defaults.article_catalog_refine_candidates) == (6, 60)
    assert off.article_catalog_refine_articles == 0
