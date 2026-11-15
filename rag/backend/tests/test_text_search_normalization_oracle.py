"""質問の側と索引の側の文字の正規化が、実 Oracle Text で対応していることを確かめる（#5116）。

規則ごとに両方向（質問がこの形・文書が別の形と、その逆）を確かめる。

- 質問の側: ``normalize_text_search_text``（NFKC → 波線・ダッシュの同一視 → 空白の連続を
  1 つ → 制御文字を空白 → ``casefold`` → 前後の空白を除く）で語を作り、
  ``_oracle_text_query`` が CONTAINS の式にする。
- 索引の側: ``_text_search_index_text``（= ``character_forms.fold_text``。NFKC → 波線・
  ダッシュ → 制御文字を空白）。改行と大文字小文字は残し、語の区切りと大文字小文字の無視は
  WORLD_LEXER に任せる（#1336・#1350）。

事例はすべて合成で、質問は規則で形が変わる語だけにする（ほかの語で当たると、規則を確かめた
ことにならない）。

- 純粋な関数: 質問の正規化・索引に入る文字列・式に入る語の表（実 DB は使わない）。
- 実 Oracle の一時表: 本番と同じ lexer / stoplist の CONTEXT 索引
  （``oracle_text_index_parameters_sql``）に全事例を入れ、事例ごとに当たるかを確かめる。
- 本番の検索の入口: 取込の保存（``save_index`` の ``_chunk_search_text``）と
  ``search_many`` の keyword を、分離 SQL（``rag_chunks_text_idx``）と原生 hybrid
  （``DBMS_HYBRID_VECTOR.SEARCH`` の ``text_contains``）の両方で通す。

当たるべきなのに当たらない食い違いは別の Issue にし、その事例は期待の振る舞いのまま
``xfail(strict=True)`` にする（直ったら strict で失敗するので、印を外す）。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest
from rag_engine.retrieval.text_search_tokenizer import normalize_text_search_text

from app.clients.oracle import (
    OracleClient,
    _oracle_text_query,
    _text_search_index_text,
    oracle_text_index_parameters_sql,
)
from app.config import Settings, get_settings
from app.rag.chunking import Chunk
from app.schemas.search import SearchMode
from tests import _oracle_test_db
from tests.test_answer_scope_oracle import _indexed_document

# 食い違いの Issue。
CASE_FOLD_ISSUE = 5117  # casefold と Oracle Text の大文字の変換の食い違い
INVISIBLE_ISSUE = 5118  # C1 制御文字と書式文字の扱いの食い違い
SCRIPT_ISSUE = 5119  # 質問の語の分割が落とす文字種


@dataclass(frozen=True)
class Case:
    """1 つの事例。``term`` は質問の式に入るはずの語（規則で形が変わった語の、正規化の後の形）。"""

    id: str
    rule: str
    document: str
    question: str
    question_normalized: str
    index_text: str
    term: str | None
    issue: int | None = None


_DASHES = "－‐‑‒–—―−"
_WAVES = "〜～~"

CASES: tuple[Case, ...] = (
    # 1. NFKC（全角英数字・全角記号・半角カナ・丸数字・ローマ数字・㈱・㌔・℃・全角空白）
    Case(
        "nfkc-fullwidth-alnum-doc",
        "NFKC",
        "型番ＱＸ７０１の説明",
        "qx701",
        "qx701",
        "型番QX701の説明",
        "qx701",
    ),
    Case(
        "nfkc-fullwidth-alnum-question",
        "NFKC",
        "型番qx702の説明",
        "ＱＸ７０２",
        "qx702",
        "型番qx702の説明",
        "qx702",
    ),
    Case(
        "nfkc-fullwidth-period-doc",
        "NFKC",
        "倍率は１．５です",
        "1.5",
        "1.5",
        "倍率は1.5です",
        "1.5",
    ),
    Case(
        "nfkc-fullwidth-period-question",
        "NFKC",
        "倍率は2.5です",
        "２．５",
        "2.5",
        "倍率は2.5です",
        "2.5",
    ),
    Case(
        "nfkc-fullwidth-underscore-doc",
        "NFKC",
        "設定ｖａｌ＿ｍａｘを使う",
        "val_max",
        "val_max",
        "設定val_maxを使う",
        "val_max",
    ),
    Case(
        "nfkc-fullwidth-underscore-question",
        "NFKC",
        "設定val_minを使う",
        "ｖａｌ＿ｍｉｎ",
        "val_min",
        "設定val_minを使う",
        "val_min",
    ),
    Case(
        "nfkc-halfwidth-dakuten-doc",
        "NFKC",
        "ｶﾞｲﾄﾞﾌﾞｯｸを配布",
        "ガイドブック",
        "ガイドブック",
        "ガイドブックを配布",
        "ガイドブック",
    ),
    Case(
        "nfkc-halfwidth-dakuten-question",
        "NFKC",
        "ガイドブックを配布",
        "ｶﾞｲﾄﾞﾌﾞｯｸ",
        "ガイドブック",
        "ガイドブックを配布",
        "ガイドブック",
    ),
    Case(
        "nfkc-halfwidth-handakuten-doc",
        "NFKC",
        "ﾊﾟｽﾜｰﾄﾞを変更",
        "パスワード",
        "パスワード",
        "パスワードを変更",
        "パスワード",
    ),
    Case(
        "nfkc-halfwidth-handakuten-question",
        "NFKC",
        "パスワードを変更",
        "ﾊﾟｽﾜｰﾄﾞ",
        "パスワード",
        "パスワードを変更",
        "パスワード",
    ),
    Case("nfkc-circled-digit-doc", "NFKC", "工程⑫で確認", "12", "12", "工程12で確認", "12"),
    Case("nfkc-circled-digit-question", "NFKC", "工程13で確認", "⑬", "13", "工程13で確認", "13"),
    Case("nfkc-roman-numeral-doc", "NFKC", "付録Ⅻを参照", "XII", "xii", "付録XIIを参照", "xii"),
    Case("nfkc-roman-numeral-question", "NFKC", "付録XIを参照", "Ⅺ", "xi", "付録XIを参照", "xi"),
    Case(
        "nfkc-parenthesized-kanji-doc",
        "NFKC",
        "表記は㈱とする",
        "株",
        "株",
        "表記は(株)とする",
        "株",
    ),
    # 「(株)」だけの質問は語にならない（Sudachi の分割）。後ろの語と分かれることを確かめる。
    Case(
        "nfkc-parenthesized-kanji-question",
        "NFKC",
        "(株)テストラボ",
        "㈱テストラボ",
        "(株)テストラボ",
        "(株)テストラボ",
        "テストラボ",
    ),
    Case(
        "nfkc-square-unit-doc", "NFKC", "上限は５㌔です", "キロ", "キロ", "上限は5キロです", "キロ"
    ),
    Case(
        "nfkc-square-unit-question",
        "NFKC",
        "上限は5キロです",
        "㌔",
        "キロ",
        "上限は5キロです",
        "キロ",
    ),
    Case("nfkc-degree-celsius-doc", "NFKC", "単位は℃で表す", "°C", "°c", "単位は°Cで表す", "c"),
    Case(
        "nfkc-degree-celsius-question", "NFKC", "単位は°Cで表す", "℃", "°c", "単位は°Cで表す", "c"
    ),
    Case(
        "nfkc-ideographic-space-doc", "NFKC", "語ｑｒｓ　ｔｕｖ", "tuv", "tuv", "語qrs tuv", "tuv"
    ),
    Case(
        "nfkc-ideographic-space-question",
        "NFKC",
        "語qrt tuw",
        "ｑｒｔ　ｔｕｗ",
        "qrt tuw",
        "語qrt tuw",
        "tuw",
    ),
    # 2. 波線・ダッシュの同一視（SYMBOL_FOLD）。型番の語の中のダッシュ。
    *(
        Case(
            f"symbol-dash-u{ord(dash):04x}-doc",
            "SYMBOL_FOLD",
            f"部品dx{dash}{10 + index}を交換",
            f"dx-{10 + index}",
            f"dx-{10 + index}",
            f"部品dx-{10 + index}を交換",
            f"dx-{10 + index}",
        )
        for index, dash in enumerate(_DASHES)
    ),
    *(
        Case(
            f"symbol-dash-u{ord(dash):04x}-question",
            "SYMBOL_FOLD",
            f"部品dy-{20 + index}を交換",
            f"dy{dash}{20 + index}",
            f"dy-{20 + index}",
            f"部品dy-{20 + index}を交換",
            f"dy-{20 + index}",
        )
        for index, dash in enumerate(_DASHES)
    ),
    Case(
        "symbol-dash-ascii",
        "SYMBOL_FOLD",
        "部品dz-30を交換",
        "dz-30",
        "dz-30",
        "部品dz-30を交換",
        "dz-30",
    ),
    *(
        Case(
            f"symbol-wave-u{ord(wave):04x}",
            "SYMBOL_FOLD",
            f"範囲は{41 + index * 2}{wave}{42 + index * 2}",
            f"{41 + index * 2}{_WAVES[(index + 1) % 3]}{42 + index * 2}",
            f"{41 + index * 2}~{42 + index * 2}",
            f"範囲は{41 + index * 2}~{42 + index * 2}",
            f"{42 + index * 2}",
        )
        for index, wave in enumerate(_WAVES)
    ),
    # 3. 空白（全角空白・タブ・改行・連続の空白）
    Case(
        "space-mixed-question",
        "空白",
        "語wsa wsb",
        "ｗｓａ　\t\n  ｗｓｂ",
        "wsa wsb",
        "語wsa wsb",
        "wsb",
    ),
    Case(
        "space-tab-newline-doc",
        "空白",
        "語wsc\t\twsd\nwse",
        "wsd",
        "wsd",
        "語wsc\t\twsd\nwse",
        "wsd",
    ),
    # 4. 制御文字
    Case("control-c0-doc", "制御文字", "語ctla\x01ctlb", "ctlb", "ctlb", "語ctla ctlb", "ctlb"),
    Case(
        "control-c0-run-doc",
        "制御文字",
        "語ctlc\x1f\x1bctld",
        "ctld",
        "ctld",
        "語ctlc ctld",
        "ctld",
    ),
    Case("control-del-doc", "制御文字", "語ctle\x7fctlf", "ctlf", "ctlf", "語ctle ctlf", "ctlf"),
    Case(
        "control-c0-question",
        "制御文字",
        "語ctlg ctlh",
        "ctlg\x07ctlh",
        "ctlg ctlh",
        "語ctlg ctlh",
        "ctlh",
    ),
    Case(
        "control-c1-doc",
        "制御文字",
        "語ctli\x85ctlj",
        "ctlj",
        "ctlj",
        "語ctli\x85ctlj",
        "ctlj",
        INVISIBLE_ISSUE,
    ),
    Case(
        "control-c1-same-form",
        "制御文字",
        "語ctlk\x85ctll",
        "ctlk\x85ctll",
        "ctlk ctll",
        "語ctlk\x85ctll",
        "ctll",
        INVISIBLE_ISSUE,
    ),
    Case(
        "format-soft-hyphen-doc",
        "書式文字",
        "語fmt­one",
        "fmtone",
        "fmtone",
        "語fmt­one",
        "fmtone",
        INVISIBLE_ISSUE,
    ),
    Case(
        "format-zwsp-doc",
        "書式文字",
        "語fmt​two",
        "fmttwo",
        "fmttwo",
        "語fmt​two",
        "fmttwo",
        INVISIBLE_ISSUE,
    ),
    Case(
        "format-bom-doc",
        "書式文字",
        "語fmt﻿six",
        "fmtsix",
        "fmtsix",
        "語fmt﻿six",
        "fmtsix",
        INVISIBLE_ISSUE,
    ),
    # 5. 大文字・小文字
    Case(
        "case-ascii-upper-doc",
        "大文字小文字",
        "語CASEALPHA",
        "casealpha",
        "casealpha",
        "語CASEALPHA",
        "casealpha",
    ),
    Case(
        "case-ascii-upper-question",
        "大文字小文字",
        "語casebeta",
        "CASEBETA",
        "casebeta",
        "語casebeta",
        "casebeta",
    ),
    Case(
        "case-fullwidth-upper-doc",
        "大文字小文字",
        "語ＣＡＳＥＧＡＭＭＡ",
        "casegamma",
        "casegamma",
        "語CASEGAMMA",
        "casegamma",
    ),
    Case(
        "case-fullwidth-upper-question",
        "大文字小文字",
        "語casedelta",
        "ＣＡＳＥＤＥＬＴＡ",
        "casedelta",
        "語casedelta",
        "casedelta",
    ),
    Case(
        "case-fullwidth-lower-doc",
        "大文字小文字",
        "語ｃａｓｅｅｐｓ",
        "CASEEPS",
        "caseeps",
        "語caseeps",
        "caseeps",
    ),
    Case(
        "case-greek-upper-doc",
        "大文字小文字",
        "語 ΚΕΙΜΕΝΟ",
        "κειμενο",
        "κειμενο",
        "語 ΚΕΙΜΕΝΟ",
        "κειμενο",
    ),
    Case(
        "case-cyrillic-upper-doc", "大文字小文字", "語 ТЕКСТ", "текст", "текст", "語 ТЕКСТ", "текст"
    ),
    Case(
        "case-greek-final-sigma-upper-doc",
        "大文字小文字",
        "語 ΛΟΓΟΣ",
        "λογος",
        "λογοσ",
        "語 ΛΟΓΟΣ",
        "λογοσ",
    ),
    Case(
        "case-greek-final-sigma-doc",
        "大文字小文字",
        "語 λογος",
        "ΛΟΓΟΣ",
        "λογοσ",
        "語 λογος",
        "λογοσ",
    ),
    Case(
        "case-greek-final-sigma-same-form",
        "大文字小文字",
        "語 νομος",
        "νομος",
        "νομοσ",
        "語 νομος",
        "νομοσ",
    ),
    Case(
        "case-sharp-s-upper-doc",
        "大文字小文字",
        "語 STRASSE",
        "Straße",
        "strasse",
        "語 STRASSE",
        "strasse",
    ),
    Case(
        "case-sharp-s-same-form",
        "大文字小文字",
        "語 Straße",
        "Straße",
        "strasse",
        "語 Straße",
        "strasse",
        CASE_FOLD_ISSUE,
    ),
    Case(
        "case-sharp-s-doc",
        "大文字小文字",
        "語 Straße",
        "strasse",
        "strasse",
        "語 Straße",
        "strasse",
        CASE_FOLD_ISSUE,
    ),
    Case(
        "case-capital-sharp-s-same-form",
        "大文字小文字",
        "語 STRAẞE",
        "STRAẞE",
        "strasse",
        "語 STRAẞE",
        "strasse",
        CASE_FOLD_ISSUE,
    ),
    Case(
        "case-dotted-capital-i-same-form",
        "大文字小文字",
        "語 İZMİR",
        "İZMİR",
        "i̇zmi̇r",
        "語 İZMİR",
        "i̇zmi̇r",
        CASE_FOLD_ISSUE,
    ),
    Case(
        "case-capital-y-diaeresis-same-form",
        "大文字小文字",
        "語 ŸVES",
        "ŸVES",
        "ÿves",
        "語 ŸVES",
        "ÿves",
        CASE_FOLD_ISSUE,
    ),
    # 6. 前後の空白（strip）
    Case(
        "strip-question",
        "前後の空白",
        "語stripone",
        "  　\tstripone \n ",
        "stripone",
        "語stripone",
        "stripone",
    ),
    Case(
        "strip-doc",
        "前後の空白",
        "  striptwo\t\n",
        "striptwo",
        "striptwo",
        "  striptwo\t\n",
        "striptwo",
    ),
    # 7. 組み合わせ
    Case(
        "combo-fullwidth-model-doc",
        "組み合わせ",
        "型番ＡＢ－１２３の手順",
        "ab-123",
        "ab-123",
        "型番AB-123の手順",
        "ab-123",
    ),
    Case(
        "combo-fullwidth-model-question",
        "組み合わせ",
        "型番cd-456の手順",
        "ＣＤ－４５６",
        "cd-456",
        "型番cd-456の手順",
        "cd-456",
    ),
    Case(
        "combo-fullwidth-emdash-hyphen",
        "組み合わせ",
        "型番ＥＦ—７８９",
        "ef‐789",
        "ef-789",
        "型番EF-789",
        "ef-789",
    ),
    Case(
        "combo-space-fullwidth-dash",
        "組み合わせ",
        "型番gh‒012",
        "　ＧＨ－０１２\t",
        "gh-012",
        "型番gh-012",
        "gh-012",
    ),
    Case(
        "combo-halfwidth-kana-fullwidth-model",
        "組み合わせ",
        "ﾓﾃﾞﾙＸＹ－３４５",
        "xy‑345",
        "xy-345",
        "モデルXY-345",
        "xy-345",
    ),
    Case(
        "combo-control-fullwidth-question",
        "組み合わせ",
        "型番jk-778",
        "\x01ＪＫ―７７８\x02",
        "jk-778",
        "型番jk-778",
        "jk-778",
    ),
    # 質問の語の分割が落とす文字種（質問の式に語が入らない）
    Case(
        "script-georgian-same-form",
        "文字種",
        "語 ტექსტი",
        "ტექსტი",
        "ტექსტი",
        "語 ტექსტი",
        "ტექსტი",
        SCRIPT_ISSUE,
    ),
    Case(
        "script-polytonic-greek-same-form",
        "文字種",
        "語 ἀρχή",
        "ἀρχή",
        "ἀρχή",
        "語 ἀρχή",
        "ἀρχή",
        SCRIPT_ISSUE,
    ),
)


def _params(cases: tuple[Case, ...]) -> list[Any]:
    return [
        pytest.param(
            case,
            id=case.id,
            marks=[
                pytest.mark.xfail(
                    strict=True,
                    raises=AssertionError,
                    reason=f"#{case.issue} の食い違い（直ったら xfail を外す）",
                )
            ]
            if case.issue is not None
            else [],
        )
        for case in cases
    ]


def _settings() -> Settings:
    return Settings(rag_domain_keywords=[])


def test_case_ids_are_unique() -> None:
    assert len({case.id for case in CASES}) == len(CASES)


@pytest.mark.parametrize("case", [pytest.param(case, id=case.id) for case in CASES])
def test_normalization_table(case: Case) -> None:
    """質問の正規化・索引に入る文字列・式に入る語の表（実 DB は使わない）。"""
    assert normalize_text_search_text(case.question) == case.question_normalized
    assert _text_search_index_text(case.document) == case.index_text
    query = _oracle_text_query(case.question, settings=_settings())
    if case.issue == SCRIPT_ISSUE:
        # 語の分割が語を落とす（#5119）。直ったら式に語が入る。
        assert query is None or "{" + str(case.term) + "}" not in query
        return
    assert query is not None
    assert "{" + str(case.term) + "}" in query


class _TextIndex:
    """全事例の文書を入れた一時表と CONTEXT 索引（初めて使うときに作り、module の最後に消す）。"""

    def __init__(self) -> None:
        self.table = f"RAG_TMP_NORM_{uuid4().hex[:8].upper()}"
        self._connection: Any = None

    def hit(self, case: Case, query: str) -> bool:
        if self._connection is None:
            self._create()
        cursor = self._connection.cursor()
        cursor.execute(
            f"SELECT COUNT(*) FROM {self.table} "
            "WHERE case_id = :case_id AND CONTAINS(body, :q, 1) > 0",
            {"case_id": case.id, "q": query},
        )
        row = cursor.fetchone()
        return bool(row and row[0])

    def _create(self) -> None:
        connection = _oracle_test_db._connect()
        cursor = connection.cursor()
        cursor.execute(
            f"CREATE TABLE {self.table} (case_id VARCHAR2(64) PRIMARY KEY, body VARCHAR2(400))"
        )
        self._connection = connection
        cursor.executemany(
            f"INSERT INTO {self.table} (case_id, body) VALUES (:1, :2)",
            [(case.id, _text_search_index_text(case.document)) for case in CASES],
        )
        connection.commit()
        cursor.execute(
            f"CREATE INDEX {self.table}_TX ON {self.table} (body) INDEXTYPE IS CTXSYS.CONTEXT "
            + oracle_text_index_parameters_sql()
        )

    def drop(self) -> None:
        if self._connection is None:
            return
        try:
            self._connection.cursor().execute(f"DROP TABLE {self.table} PURGE")
        finally:
            self._connection.close()


@pytest.fixture(scope="module")
def text_index() -> Iterator[_TextIndex]:
    index = _TextIndex()
    yield index
    index.drop()


@pytest.mark.usefixtures("oracle_db")
@pytest.mark.parametrize("case", _params(CASES))
def test_question_terms_hit_normalized_index_on_real_oracle(
    case: Case, text_index: _TextIndex
) -> None:
    """本番と同じ lexer / stoplist の CONTEXT 索引で、質問の式が索引した文書に当たる。"""
    query = _oracle_text_query(case.question, settings=_settings())
    assert query is not None, f"{case.id}: 質問の式が空"
    assert text_index.hit(case, query), f"{case.id}: {query!r} が {case.index_text!r} に当たらない"


def _expected_entry_miss(case: Case, backend: str) -> bool:
    """本番の検索の入口で当たらないはずの事例（食い違いの Issue のある事例）。"""
    if case.issue is None:
        return False
    # 原生 hybrid は、語が 1 つも無い質問を原文のまま全文検索に渡す（``_native_rows``）ので、
    # Oracle Text が原文を語に分けて当たる。分離 SQL は語が無ければ全文検索をしない。
    return not (
        backend == "oracle_hybrid_index"
        and _oracle_text_query(case.question, settings=_settings()) is None
    )


@pytest.mark.usefixtures("oracle_db")
@pytest.mark.parametrize("backend", ["oracle_separate_sql", "oracle_hybrid_index"])
async def test_keyword_search_entry_hits_saved_chunks_on_real_oracle(
    backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """取込の保存（``_chunk_search_text``）と ``search_many`` の keyword を通す。

    分離 SQL と原生 hybrid の両方で、事例ごとに 1 つの chunk（page_number で見分ける）を
    1 つの文書に入れ、文書で絞って検索する。食い違いの事例（``issue`` あり）は当たらない
    ことを確かめる（直ったら失敗するので、その事例の印を外す）。
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_hybrid_search_backend", backend)
    monkeypatch.setattr(settings, "rag_domain_keywords", [])
    oracle = OracleClient(settings)
    chunks = [
        Chunk(
            text=case.document,
            index=index,
            start_offset=0,
            end_offset=len(case.document),
            metadata={"page_number": index + 1},
        )
        for index, case in enumerate(CASES)
    ]
    document_id = await _indexed_document(
        oracle, file_name=f"synthetic-text-normalization-{uuid4().hex}.txt", chunks=chunks
    )
    rankings = await oracle.search_many(
        [(case.question, [], SearchMode.KEYWORD) for case in CASES],
        len(CASES),
        {"document_id": document_id},
    )
    hits: dict[str, bool] = {}
    for index, (case, ranking) in enumerate(zip(CASES, rankings, strict=True)):
        failure = getattr(ranking, "failure", None)
        if failure is not None:
            raise AssertionError(f"{backend}/{case.id}: 検索に失敗しました") from failure
        hits[case.id] = any(chunk.metadata.get("page_number") == index + 1 for chunk in ranking)
    misses = sorted(case_id for case_id, hit in hits.items() if not hit)
    expected = sorted(case.id for case in CASES if _expected_entry_miss(case, backend))
    assert misses == expected, f"{backend}: 当たらない {misses} / 期待 {expected}"
