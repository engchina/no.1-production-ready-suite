"""回答の段落の分類と、本文の整形の部品のテスト（#4714 で最終の検証のテストから分けた）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.features.agent.answer_passages import (
    is_absence_only,
    is_heading,
    is_operation_step,
    label_operation_end,
    list_heading_lines,
    list_item_label,
    non_claim_passages,
    passage_kind,
    passage_spans,
)
from app.features.agent.answer_references import withhold_paragraphs

# 根拠が見つからなかった rag_search の後の拒答（#1879。#1863 の実モデルの確認
# run_0c19e37a48ad42a09ea2584abb760b97）。
ABSENCE_ANSWER = (
    "調査しましたが、利用可能な業務資料・データベースの中に「日本の首都」についての根拠は"
    "見つかりませんでした。したがって、資料に基づく回答はできません。ご了承ください。"
)


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (ABSENCE_ANSWER, True),
        ("資料に記載がありません。申し訳ありません。", True),
        ("- 取得した資料からは、今の設定値は確かめられません。\n何卒ご了承ください。", True),
        # 結びだけ・主張・逆接・数量・操作を含むものは拒答だけの回答にしない。
        ("ご了承ください。", False),
        ("申し訳ありませんが、管理画面で削除してください。", False),
        ("日本の首都は東京です。資料には記載がありません。", False),
        ("ライセンス費用は月額 1,000 円で、資料には記載がありません。", False),
        ("資料に記載がありません。設定画面で「削除」を押してください。", False),
        ("", False),
    ],
    ids=[
        "refusal",
        "apology",
        "bullet-and-closing",
        "closing-only",
        "apology-with-operation",
        "claim",
        "quantity",
        "operation",
        "empty",
    ],
)
def test_absence_only_is_conservative(answer: str, expected: bool) -> None:
    assert is_absence_only(answer) is expected


def test_withhold_removes_only_the_listed_paragraphs() -> None:
    answer = "会費は無料です。\n無料です。\n手順: 開く。押す。閉じる。"

    # 同じ文を含む別の段落（「会費は無料です。」）は削らない。行の中の段落だけを外す。
    assert (
        withhold_paragraphs(answer, {"無料です。", "押す。"})
        == "会費は無料です。\n手順: 開く。閉じる。"
    )
    # 位置を決められない段落があれば本文を載せない。
    assert withhold_paragraphs(answer, {"どこにも無い。"}) is None


def test_withhold_removes_numbered_headings_of_emptied_sections_and_renumbers() -> None:
    """中身をすべて外した節の見出しは、番号つきでも消し、残った項目の番号を詰める（#1898・#2692）。

    業務 Agent の確認 #1863（run_839d27d75e224dd39e9bb2fdecee1c99）の形。「1. **承認の有無の
    確認**」の下の段落を外すと、本文が「2. **削除手順の要件**」から始まっていた（#1898 は見出しを
    残したが、中身の無い名前が実行の順の 1 つとして読めるので、#2692 で消して番号を詰める）。
    番号の無い見出し（「**補足**」）は今までどおり消す。
    """
    answer = (
        "**手順（部門長の承認がない場合）**\n\n"
        "1. **承認の有無の確認**\n"
        "   - 利用者の回答により、部門長の承認は取得されていません。\n\n"
        "2. **削除手順の要件**\n"
        "   - アカウントを削除するには、所属部署の部門長の承認が必要です。\n\n"
        "**補足**\n"
        "- 削除は 1 日で終わります。"
    )

    kept = withhold_paragraphs(
        answer,
        {"- 利用者の回答により、部門長の承認は取得されていません。", "- 削除は 1 日で終わります。"},
    )

    assert kept == (
        "**手順（部門長の承認がない場合）**\n\n"
        "1. **削除手順の要件**\n"
        "   - アカウントを削除するには、所属部署の部門長の承認が必要です。"
    )


@pytest.mark.parametrize(
    ("answer", "quotes", "expected"),
    [
        # 外した段落の直後の段落の、前の段落を受ける接続の語を外す（同じ行・次の行・見出しを
        # 挟んでも）。
        (
            "締め日は 25 日です。したがって、処置は不明です。",
            {"締め日は 25 日です。"},
            "処置は不明です。",
        ),
        (
            "締め日は 25 日です。\nそのため、処置は不明です。",
            {"締め日は 25 日です。"},
            "処置は不明です。",
        ),
        (
            "締め日は 25 日です。\n\n**結論**\n\nつまり、処置は不明です。",
            {"締め日は 25 日です。"},
            "**結論**\n\n処置は不明です。",
        ),
        # 意味を変える語（ただし・なお）は残す。
        (
            "締め日は 25 日です。ただし、処置は不明です。",
            {"締め日は 25 日です。"},
            "ただし、処置は不明です。",
        ),
        # 直前の段落を残した段落の接続の語は変えない。
        (
            "締め日は 25 日です。延長は無い。したがって、処置は不明です。",
            {"延長は無い。"},
            "締め日は 25 日です。処置は不明です。",
        ),
        (
            "延長は無い。締め日は 25 日です。したがって、処置は不明です。",
            {"延長は無い。"},
            "締め日は 25 日です。したがって、処置は不明です。",
        ),
    ],
    ids=[
        "same-line",
        "next-line",
        "across-heading",
        "keeps-tadashi",
        "after-withheld",
        "after-kept",
    ],
)
def test_withhold_drops_dangling_connectives(answer: str, quotes: set[str], expected: str) -> None:
    """外した段落を受ける接続の語（「したがって、」など）から本文が始まらない（#1833）。"""
    assert withhold_paragraphs(answer, quotes) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1. 対象の情報を訂正する。", True),
        ("- 管理者に申請してください。", True),
        ("2. **申請書を提出します**（第 2 章）。", True),
        ("3. 対象を確認しておく。", True),
        ("1. 識別子は 12 桁です。", False),
        ("- 延長は最長 90 日までできます。", False),
        ("2. 処理の中で通知が発生します。", False),
        ("3. 承認者は管理者である。", False),
    ],
)
def test_operation_steps_are_decided_by_the_predicate_form(text: str, expected: bool) -> None:
    """手順の項目かは、操作の指示か文末の指示の形で決める（業務の語は見ない。#2604）。"""
    assert is_operation_step(text) is expected


def test_heading_keeps_its_number_while_correct_content_remains() -> None:
    """見出しの下に正しい内容が残る項目は、見出しも番号も残す（下の内容を消さない。#2692）。
    中身をすべて外した見出しは消し、続く項目の番号を詰める。"""
    answer = "\n".join(
        [
            "1. **属性訂正**",
            "   - 対象 A の属性を先に訂正する。",
            "   - 訂正の記録は 5 年間保存されます。",
            "2. **記録の保存**",
            "   - 保存期間の満了後は記録を消去できます。",
        ]
    )
    quote = "- 対象 A の属性を先に訂正する。"
    assert withhold_paragraphs(answer, {quote}) == "\n".join(
        [
            "1. **属性訂正**",
            "   - 訂正の記録は 5 年間保存されます。",
            "2. **記録の保存**",
            "   - 保存期間の満了後は記録を消去できます。",
        ]
    )
    assert withhold_paragraphs(answer, {quote, "- 訂正の記録は 5 年間保存されます。"}) == (
        "1. **記録の保存**\n   - 保存期間の満了後は記録を消去できます。"
    )


@pytest.mark.parametrize(
    ("marks", "expected"),
    [
        (["１．", "２．", "３．"], ["１．", "２．"]),
        (["（1）", "（2）", "（3）"], ["（1）", "（2）"]),
        (["①", "②", "③"], ["①", "②"]),
    ],
)
def test_renumbering_keeps_the_number_style(marks: list[str], expected: list[str]) -> None:
    """番号を詰めるときは、全角の数字・括弧・丸数字の番号の形を保つ（#2692）。"""
    items = ["事実の 1 つ目です。", "事実の 2 つ目です。", "事実の 3 つ目です。"]
    answer = "\n".join(f"{mark}{item}" for mark, item in zip(marks, items, strict=True))
    kept = withhold_paragraphs(answer, {f"{marks[0]}{items[0]}"})
    assert kept == "\n".join(
        f"{mark}{item}" for mark, item in zip(expected, items[1:], strict=True)
    )


def test_numbers_are_not_renumbered_when_the_body_refers_to_them() -> None:
    """本文が番号で項目を指していれば（「手順 3 の後に」）、番号を詰めない（指す先が変わるため。
    #2692）。"""
    answer = (
        "1. 事実の 1 つ目です。\n2. 事実の 2 つ目です。\n3. 事実の 3 つ目です。\n"
        "手順 3 の後に連絡する。"
    )
    assert withhold_paragraphs(answer, {"1. 事実の 1 つ目です。"}) == (
        "2. 事実の 2 つ目です。\n3. 事実の 3 つ目です。\n手順 3 の後に連絡する。"
    )


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("## 確認手順", "heading"),
        ("**手順**", "heading"),
        # 「確認が必要な点」は最終の検証が足す節の見出し（#2738。unverified_section）。
        ("注意が必要な点：", "heading"),
        ("【portal-operations-manual.pdf, section 2. アカウント管理, page 1】", "citation"),
        ("- 出典：admin-guide.pdf 第3章", "citation"),
        ("（参照: security-policy.docx p.4）", "citation"),
        ("[運用マニュアル](https://example.com/manual.pdf)", "citation"),
        ("所属部署の部門長の承認は得ていますか？（はい／いいえ）", "question"),
        ("付与先の部署名を教えてください。", "question"),
        ("ライセンス費用については、資料に記載がありません。", "absence"),
        ("取得した資料からは、今の設定値は確かめられません。", "absence"),
        # 主張・操作の手順（外す対象のまま）。
        ("**30 日以内に申請してください**", None),
        ("## 管理画面で「削除」を押します", None),
        ("根拠: 契約書の第 5 条により、30 日前までに申し出る必要があります", None),
        ("削除できますが、よろしいですか？", None),
        ("ライセンス費用は月額 1,000 円で、資料には記載がありません。", None),
        ("資料には記載がありませんが、設定画面で削除できます。", None),
        ("管理画面で「削除」を押してください。", None),
        ("パスワードは 90 日ごとに変更します。", None),
    ],
    ids=[f"case{index}" for index in range(19)],
)
def test_passage_kind_is_conservative(text: str, kind: str | None) -> None:
    assert non_claim_passages(text).get(text) == kind


EVIDENCE_REF = (
    "【証拠 ID: 03ac895f1d744a2b9eab4dd569eb68fc:"
    "b245283c90ea80e5d8aadd3283e6027f148113c0aa42c59bbf12923cc59ae01c:1】"
)


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        # 出典の位置のラベル（#1317。da-trial-account-expiry）。
        (
            "- セクション: 「サンプル業務ポータル 運用手順書 第3版」 → "
            "**2. 検証用アカウントの登録**",
            "citation",
        ),
        ("- ページ: 1  ", "citation"),
        ("- **根拠**：regional-report-guide-v2.pdf、2. 出力の手順（ページ1）", "citation"),
        # 表のセルに分かれた出典（da-trial-account-setup）。
        (f"| 「1. 前提」 （ページ1）{EVIDENCE_REF} |", "citation"),
        (
            "（*portal-operations-manual.pdf*、セクション「3. アクセス権限の付与」）【証拠1】",
            "citation",
        ),
        ("- 「サンプル業務ポータル 運用手順書 第3版」 6. アカウントの削除", "citation"),
        # 表の区切りの行・区切り線。
        ("|------|----------|------|", "table"),
        ("| :--- | ---: |", "table"),
        ("---", "table"),
        # 拒答の文（km-license-fee・km-password-policy）と、検索結果に該当が無かった文。
        ("推測で金額を示すことはできません。", "absence"),
        (
            "そのため、パスワードは何文字以上にすべきかについて根拠のある回答はできません。",
            "absence",
        ),
        (
            "- rag__rag_lookup_guides の検索結果でも、今回の質問に該当する業務ガイドは"
            "返ってきませんでした。",
            "absence",
        ),
        # 主張のまま（外す対象）。
        ("- ページ: 管理画面で「削除」を押してください", None),
        ("- 抜粋: 「…有効期限は最長 30 日です。」", None),
        ("- 「有効期限は最長 30 日です。」", None),
        ("- 「検証用」", None),
        ("| 締め日 | 毎月 10 日 |", None),
        ("この機能では、アカウントを削除することはできません。", None),
        ("利用者を検索してもヒットしません。", None),
        ("年間ライセンス費用は 120,000 円のため、それ以上の金額はお示しできません。", None),
        (f"| 2. 登録 | 1. 「利用者」を開く 2. 「追加」を押す | {EVIDENCE_REF} |", None),
    ],
    ids=[f"case{index}" for index in range(21)],
)
def test_citations_tables_and_refusals_are_not_claims(text: str, kind: str | None) -> None:
    # #1317: #1306 の判定から漏れて unassessed で外れ、回答の対応を誤らせていた段落。
    assert non_claim_passages(text).get(text.strip()) == kind


TABLE_HEADER = "| 手順 | 操作内容 | 根拠 |"


TABLE_SEPARATOR = "|------|----------|------|"


TABLE_ROW = "| 1. 前提確認 | 「ポータル管理者」ロールが要ります | 「1. 前提」（ページ1） |"


def test_table_header_is_structure_only_before_a_separator() -> None:
    answer = f"検証用アカウントの登録の手順です。\n\n{TABLE_HEADER}\n{TABLE_SEPARATOR}\n{TABLE_ROW}"
    kinds = non_claim_passages(answer)
    assert kinds[TABLE_HEADER] == "table"
    assert kinds[TABLE_SEPARATOR] == "table"
    # 表の本文の行は主張のまま。区切りの行が後ろに無い行は見出しにしない。
    assert TABLE_ROW not in kinds
    assert TABLE_HEADER not in non_claim_passages(f"{TABLE_HEADER}\n{TABLE_ROW}")
    # 本文の行をすべて外したら、表の見出しと区切りだけを本文として出さない。
    lead = "検証用アカウントの登録の手順です。"
    assert withhold_paragraphs(answer, {lead, TABLE_ROW}) == ""
    assert (
        withhold_paragraphs(answer, {TABLE_ROW}) == f"{lead}\n\n{TABLE_HEADER}\n{TABLE_SEPARATOR}"
    )


# 出典の行の事例（RAG と Agent の両方のテストが読む。#1370）。
CITATION_CASES_PATH = "platform/contracts/answer-passages/citation-lines.json"


CITATION_CASES: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[3] / CITATION_CASES_PATH).read_text("utf-8")
)


@pytest.mark.parametrize(
    "case", CITATION_CASES["citation_passages"], ids=lambda case: str(case["id"])
)
def test_citation_only_passages_are_not_claims(case: dict[str, str]) -> None:
    """定位子・根拠の ID・文書名と場所だけの行は出典の行（#1370）。"""
    text = case["passage"]
    assert passage_kind(text) == "citation"
    assert non_claim_passages(text).get(text) == "citation"


@pytest.mark.parametrize("case", CITATION_CASES["claim_passages"], ids=lambda case: str(case["id"]))
def test_passages_with_body_after_the_location_stay_claims(case: dict[str, str]) -> None:
    """場所・ラベルの後に本文が続く行は主張のまま確かめる（取りこぼさない。#1370）。"""
    text = case["passage"]
    assert passage_kind(text) is None
    assert text not in non_claim_passages(text)


# 見出しの名詞句・導入の文の事例（RAG と Agent の両方のテストが読む。#1469）。
HEADING_CASES_PATH = "platform/contracts/answer-passages/headings.json"


HEADING_CASES: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[3] / HEADING_CASES_PATH).read_text("utf-8")
)


@pytest.mark.parametrize(
    "case", HEADING_CASES["heading_passages"], ids=lambda case: str(case["id"])
)
def test_heading_phrases_and_lead_ins_are_headings(case: dict[str, str]) -> None:
    """「補足（…）」・「根拠」・「…は次のとおりです。」は見出し（#1469）。"""
    text = case["passage"]
    assert is_heading(text)
    assert passage_kind(text) == "heading"
    assert non_claim_passages(text).get(text) == "heading"


@pytest.mark.parametrize(
    "case", HEADING_CASES["numbered_heading_passages"], ids=lambda case: str(case["id"])
)
def test_numbered_headings_are_decided_by_the_following_line(case: dict[str, str]) -> None:
    """番号つきの見出しの行は、次の行が下の内容のときだけ見出し（#1481 / #1482）。

    終わりの語が一覧にある行（「1. 人事評価システムの重要度」）も、一覧に無い「…の…」の行も、
    単独の行と、番号の行が続く一覧の中の行は主張のまま確かめる。
    """
    text = case["passage"]
    for below in ("   - 資料の記載", "- 資料の記載", "  資料の記載", "→ 資料の記載"):
        answer = f"{text}\n\n{below}"
        assert list_heading_lines(answer) == {text}
        assert non_claim_passages(answer).get(text) == "heading"
    assert not is_heading(text)
    assert passage_kind(text) is None
    assert text not in non_claim_passages(text)
    listed = f"{text}\n9. 次の項目\n   - 資料の記載"
    assert text not in list_heading_lines(listed)
    assert text not in non_claim_passages(listed)
    assert list_heading_lines(f"{text}\n**次の節**") == set()


@pytest.mark.parametrize(
    "passage",
    [
        "- システム台帳（system-ledger.xlsx・シート「システム台帳」）",
        "- **システム台帳**（system-ledger.xlsx、シート「システム台帳」）",
    ],
    ids=["sheet-note", "bold-document"],
)
def test_bullet_document_with_file_note_is_a_citation(passage: str) -> None:
    """資料のファイル名の注記が付いた箇条書きの行は、次の行に関係なく出典の行（#1486）。

    e1467 の run_93a965a4・run_eff5f621 の「根拠」の行。RAG は下に字下げした行が続くときだけ見出しに
    する（`list_heading_lines`）ので、見出しの契約（headings.json）には入れず両方のテストで個別に
    確かめる。
    """
    assert passage_kind(passage) == "citation"
    for following in ("  - SYS-105 正式名: ドキュメントポータル / 重要度: B", "- 組織規程（1 頁）"):
        answer = f"**根拠**\n{passage}\n{following}"
        assert passage not in list_heading_lines(answer)
        assert non_claim_passages(answer).get(passage) == "citation"


def test_numbered_list_items_ending_with_heading_words_are_claims() -> None:
    """番号の行が続く一覧の項目は、終わりの語が見出しの語の一覧にあっても主張（#1482）。

    e1467 の run_ee5468f8 の「申請書に書く 4 項目」。#1469 の段落だけの判定では「1. 変更の内容と
    理由」「3. 切り戻しの手順」が見出しになり、主張として監査されなかった。同じ行でも、下に内容の
    行があれば見出し。
    """
    items = [
        "1. 変更の内容と理由",
        "2. 影響を受けるシステムと利用者",
        "3. 切り戻しの手順",
        "4. 作業の日時",
    ]
    answer = "**申請書に書く 4 項目**\n" + "\n".join(items) + "\n\n**条件によって加わる項目**"
    kinds = non_claim_passages(answer)
    assert list_heading_lines(answer) == set()
    assert kinds.get("**申請書に書く 4 項目**") == "heading"
    for item in items:
        assert not is_heading(item)
        assert passage_kind(item) is None
        assert item not in kinds
    below = "3. 切り戻しの手順\n   1. 旧版に戻す\n   2. 動作を確かめる"
    assert list_heading_lines(below) == {"3. 切り戻しの手順"}
    assert non_claim_passages(below).get("3. 切り戻しの手順") == "heading"
    assert "1. 旧版に戻す" not in non_claim_passages(below)


@pytest.mark.parametrize(
    "case", HEADING_CASES["bullet_heading_passages"], ids=lambda case: str(case["id"])
)
def test_bullet_headings_are_decided_by_the_following_line(case: dict[str, str]) -> None:
    """箇条書きの見出しの行は、次の行が下位の内容（深い字下げ・「→」）のときだけ見出し（#1486）。

    単独の行と、同じ字下げの箇条書きの行が続く一覧の項目（「ご確認いただきたい点」の「- 組織規程
    （ご担当部署の承認者）」）は、終わりの語が一覧にあっても主張のまま確かめる。
    """
    text = case["passage"]
    for below in ("   - 資料の記載", "  資料の記載", "→ 資料の記載"):
        answer = f"{text}\n\n{below}"
        assert list_heading_lines(answer) == {text}
        assert non_claim_passages(answer).get(text) == "heading"
    assert not is_heading(text)
    assert passage_kind(text) is None
    assert text not in non_claim_passages(text)
    for sibling in ("- 次の項目", "・次の項目", "1. 次の項目", "**次の節**"):
        listed = f"{text}\n{sibling}\n   - 資料の記載"
        assert text not in list_heading_lines(listed)
        assert text not in non_claim_passages(listed)


@pytest.mark.parametrize("case", HEADING_CASES["claim_passages"], ids=lambda case: str(case["id"]))
def test_short_statements_are_not_headings(case: dict[str, str]) -> None:
    """数・操作・言い切りを含む短い行と、値の名前だけの行は主張のまま確かめる（#1469）。

    次の行が下の内容でも見出しにしない（#1481）。
    """
    text = case["passage"]
    assert not is_heading(text)
    assert passage_kind(text) is None
    assert text not in non_claim_passages(text)
    below = f"{text}\n   - 資料の記載"
    assert list_heading_lines(below) == set()
    assert text not in non_claim_passages(below)


# 段落の分け方の事例（RAG と Agent の両方のテストが読む。#1455）。
SENTENCE_CASES_PATH = "platform/contracts/answer-passages/sentence-splits.json"


SENTENCE_CASES: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[3] / SENTENCE_CASES_PATH).read_text("utf-8")
)


@pytest.mark.parametrize("case", SENTENCE_CASES["cases"], ids=lambda case: str(case["id"]))
def test_passages_are_not_split_inside_brackets(case: dict[str, Any]) -> None:
    """括弧の中の「。」で段落を分けず、太字の印・閉じ括弧だけの断片を作らない（#1455）。"""
    line = case["line"]
    spans = passage_spans(line)
    assert [text for _start, _end, text in spans] == case["passages"]
    # 位置は行の中の原文を指す。
    assert all(line[start:end] == text for start, end, text in spans)


# dev cmp-pms-severity（run_aeda6990aad14955bb88d957deb2b6e2）の回答の台帳の節（#1455）。
_LEDGER_SECTION = "\n".join(
    [
        "- システム台帳（system-ledger.xlsx、シート「システム台帳」）",
        "  - 行1〜3（表頭の説明）: 「重要度は A・B・C の 3 段階です。"
        "A が最も高く、B、C の順に低くなります。」",
    ]
)


def test_withholding_a_quoted_passage_leaves_no_fragment() -> None:
    """括弧の中で分けた前の半分だけを外し、閉じ括弧の残る断片を本文に残さない（#1455）。

    以前は「…3 段階です。」と「A が最も高く、…」」に分かれ、前の半分を外すと
    「A が最も高く、B、C の順に低くなります。」」が本文に残っていた。
    """
    quote = _LEDGER_SECTION.split("\n")[1].strip()
    assert [text for _s, _e, text in passage_spans(_LEDGER_SECTION.split("\n")[1])] == [quote]
    published = withhold_paragraphs(_LEDGER_SECTION, {quote})
    assert published is not None
    assert "A が最も高く" not in published
    assert "」" not in published.replace("「システム台帳」", "")


@pytest.mark.parametrize(
    "case", HEADING_CASES["table_header_cases"], ids=lambda case: str(case["id"])
)
def test_table_header_contract_with_empty_cells(case: dict[str, Any]) -> None:
    from app.features.agent.answer_passages import table_header_lines

    assert table_header_lines(case["answer"]) == set(case["headers"])
    kinds = non_claim_passages(case["answer"])
    for header in case["headers"]:
        assert kinds[header] == "table"
    assert "| 値 | 10 | 20 |" not in kinds


@pytest.mark.parametrize(
    "heading", ["**上限は無制限**", "## 上限は無制限。確認してください。", "【上限は無制限】"]
)
def test_assertion_in_heading_markup_ends_the_previous_unverified_section(heading: str) -> None:
    claim = "- 上限は 30 件です。"
    answer = f"**確認が必要な点**\n- 未確認の項目\n\n{heading}\n{claim}"
    kinds = non_claim_passages(answer)
    assert heading not in kinds
    assert claim not in kinds
    assert kinds["- 未確認の項目"] == "unverified_section"

