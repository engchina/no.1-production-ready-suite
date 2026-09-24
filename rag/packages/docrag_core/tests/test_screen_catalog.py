"""画面目録で質問から画面を選び、その画面の根拠を検索候補に加える (#1108)。"""
from types import SimpleNamespace

from docrag.retrieval.screen_catalog import (LinkedScreen, ScreenLinks, build_screen_catalog, link_screens,
                                             screen_candidates, validated_links)


def _chunk(uid, source, path, *, level="child", page=1, seq=1):
    return SimpleNamespace(chunk_uid=uid, source_file_name=source, chunk_level=level, page_start=page, chunk_seq=seq,
                           metadata={"section_path": list(path)})


POOL = [
    _chunk("a1", "架空管理.pdf", ["架空管理", "（２）伝票印刷設定", "B 【操作説明】"], page=5, seq=2),
    _chunk("a0", "架空管理.pdf", ["架空管理", "（２）伝票印刷設定", "A 【画面説明】"], page=5, seq=1),
    _chunk("b1", "架空管理.pdf", ["架空管理", "（３）出力様式設定"], page=8),
    _chunk("c1", "架空仕入.pdf", ["架空仕入", "（２）伝票印刷設定"], page=3),
    _chunk("p1", "架空管理.pdf", ["架空管理", "（２）伝票印刷設定"], level="parent"),
]


def test_catalog_lists_numbered_screen_headings_per_document():
    catalog = build_screen_catalog(POOL)

    assert catalog["架空管理.pdf"][0] == "（２）伝票印刷設定"  # 出現の多い順
    assert "（３）出力様式設定" in catalog["架空管理.pdf"]
    assert "B 【操作説明】" not in catalog["架空管理.pdf"]  # 番号付きの見出しだけ
    assert "架空管理" not in catalog["架空管理.pdf"]


def test_only_links_that_exist_in_the_catalog_are_kept():
    catalog = build_screen_catalog(POOL)
    links = ScreenLinks(candidates=[LinkedScreen(document="架空管理.pdf", screen="(2)伝票印刷設定"),  # 全角半角の違いは許す
                                    LinkedScreen(document="架空管理.pdf", screen="（９）存在しない画面"),
                                    LinkedScreen(document="無い文書.pdf", screen="（２）伝票印刷設定")])

    assert validated_links(links, catalog) == [("架空管理.pdf", "（２）伝票印刷設定")]

    # 目録の表記「[文書名]」を括弧ごと写した文書名も受け入れる。
    bracketed = ScreenLinks(candidates=[LinkedScreen(document="[架空管理.pdf]", screen="（３）出力様式設定")])
    assert validated_links(bracketed, catalog) == [("架空管理.pdf", "（３）出力様式設定")]


def test_children_of_the_linked_screen_are_added_in_reading_order_without_duplicates():
    added = screen_candidates(POOL, [("架空管理.pdf", "（２）伝票印刷設定")], existing={"a1"})

    assert [c.chunk_uid for c in added] == ["a0"]  # 既存の候補・別文書・parent は加えない


def test_link_failures_leave_retrieval_unchanged():
    def broken(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    assert link_screens("帳票の印字を変えたい。", build_screen_catalog(POOL), broken, settings=None) == []
    assert link_screens("帳票の印字を変えたい。", {}, broken, settings=None) == []


def test_reserve_moves_only_the_best_child_of_each_linked_screen_up_to_the_rank():
    from docrag.retrieval.screen_catalog import reserve_linked_screens
    records = [SimpleNamespace(id=f"r{i}", source="架空管理.pdf", metadata={"section_path": ["（９）別画面"]}) for i in range(6)]
    records += [SimpleNamespace(id="s1", source="架空管理.pdf", metadata={"section_path": ["（２）伝票印刷設定"]}),
                SimpleNamespace(id="s2", source="架空管理.pdf", metadata={"section_path": ["（２）伝票印刷設定"]})]

    ordered = reserve_linked_screens(records, [("架空管理.pdf", "（２）伝票印刷設定")], rank=3)

    assert [r.id for r in ordered][:4] == ["r0", "r1", "s1", "r2"]  # 1 件だけを 3 位へ、ほかの順は保つ
    assert [r.id for r in ordered].index("s2") == 7
