"""回答の最終の検証（#1246）。モデルはスタブ。標準回答を使わず主張だけを監査する。"""

import json
from pathlib import Path

import pytest
from validation_unit_support import wire_text

EVIDENCE = [{"id": "c1", "source": "manual.pdf", "page_start": 1, "page_end": 1, "text": "検証用アカウントの有効期限は最長 30 日です。"}]
ANSWER = "検証用アカウントの有効期限は最長 30 日です。\n\n延長は 90 日までできます。"


EVIDENCE_REF = "【証拠 ID: 03ac895f1d744a2b9eab4dd569eb68fc:b245283c90ea80e5d8aadd3283e6027f148113c0aa42c59bbf12923cc59ae01c:1】"


# 多段の質問の評価の Run の形（#1364。cmp-hr-vs-attendance-retention）: 台帳の行（機密区分）と規程（保管期間）。
LEDGER = {
    "id": "doc-ledger:set:3",
    "source": "system-ledger.xlsx",
    "page_start": None,
    "page_end": None,
    "text": "システムID: SYS-103 / 正式名: 人事評価システム / 機密区分: 極秘",
}
RETENTION = {
    "id": "doc-retention:set:1",
    "source": "data-retention.pdf",
    "page_start": 1,
    "page_end": 1,
    "text": "極秘のデータは 10 年間保管します。社外秘のデータは 7 年間保管します。",
}
BRIDGE_ANSWER = "人事評価システムは「極秘」扱いなので、データは **10 年間** 保管されます。"


# #1362 の評価の Run の形（#1391）。業務支援の da-trial-account-setup: 運用手順書の章ごとの chunk と、手順の回答。
PORTAL = "d71d90b414a74ad99ccfc7a9dc4b3cb5:2e474d0310926b739eeed94bfc31012518db916cc382524721434a8baa1d629d"
PORTAL_EVIDENCE = [
    {
        "id": f"{PORTAL}:2",
        "source": "portal-operations-manual.pdf",
        "page_start": 1,
        "page_end": 1,
        "text": "2. 検証用アカウントの登録\n1.管理画面の「利用者」を開き、「追加」を押します。\n2.利用者種別で「検証用」を選びます。",
    },
    {
        "id": f"{PORTAL}:3",
        "source": "portal-operations-manual.pdf",
        "page_start": 1,
        "page_end": 1,
        "text": "3. アクセス権限の付与\n個別の利用者に付与する場合: 利用者の詳細画面の「権限」タブで、"
        "付与する権限を選んで「付与」を押します。",
    },
    {
        "id": f"{PORTAL}:4",
        "source": "portal-operations-manual.pdf",
        "page_start": 1,
        "page_end": 1,
        "text": "4. 通知の設定\n1.権限を付与した後、利用者の詳細画面の「通知」タブを開きます。",
    },
]
PORTAL_ANSWER = "\n".join(
    [
        "- 管理画面の「利用者」メニューを開き「追加」ボタンを押す。",
        "- 登録した検証用利用者の詳細画面の **「権限」タブ** を開く。",
        "- 権限付与が完了したら、同じ利用者の詳細画面の **「通知」タブ** を開く。",
        "以上の順序で実施すれば、検証用アカウントの登録から権限付与、さらに通知設定まで完了します。",
    ]
)


# 多段の br-document-portal-retention: 回答が根拠の ID を途中で省いて書き（「…fffa…:1」）、検証のモデルがそれを写した。
PORTAL_LEDGER = {
    "id": "87ae9f2fc8f944a8a78edfba9d8ac8b9:a3ff18c115fc4f2bb10aece14f0cfc09a975f6a90598cd4ccaf483042efd1814:5",
    "source": "system-ledger.xlsx",
    "page_start": None,
    "page_end": None,
    "text": "システムID: SYS-105 / 正式名: ドキュメントポータル / 担当部署: 総 / 重要度: B / 機密区分: 社内限り",
}
RETENTION_SET = "af257c62c85d45cd9211a098fdca6fb4:16f11273eede4c4f508dcc4453985fffa816db12ed7c51b18f03305bee231ace"
RETENTION_RULES = [
    {
        "id": f"{RETENTION_SET}:1",
        "source": "data-retention-rules.pdf",
        "page_start": 1,
        "page_end": 1,
        "text": "第 1 章 機密区分と保管期間\n極秘のデータは 10 年間保管します。\n社内限りのデータは 3 年間保管します。",
    },
    {
        "id": f"{RETENTION_SET}:2",
        "source": "data-retention-rules.pdf",
        "page_start": 1,
        "page_end": 1,
        "text": "第 2 章 閲覧の記録\n極秘のデータを扱うシステムは、閲覧の記録（アクセスログ）を 3 年間保管します。",
    },
]
ABBREVIATED_CHUNK_ID = "af257c62c85d45cd9211a098fdca6fb4:16f11273eede4c4f508dcc4453985fffa…:1"
RETENTION_ANSWER = "\n".join(
    [
        "ドキュメントポータル（SYS‑105）の機密区分は「社内限り」（システム台帳）であり、社内限りのデータは **3 年間** 保管します。",
        f"- データ保管規程: 「社内限りのデータは 3 年間保管します」【evidence_id {ABBREVIATED_CHUNK_ID}】",
        "したがって、ドキュメントポータルのデータの保管期間は **3 年** です。",
    ]
)


# #1362 の再評価の Run run_f45cb461c85946459d604dd08561d626（cmp-mes-vs-qms-deadline）の形（#1404。ID は合成した値）。
# 回答は根拠の chunk の id を完全な形で「【証拠 ID 文書:chunk_set:番号】」と書き、検証に渡した根拠にも同じ id がある。
MES = "5c61673c22be4d39b075223c6de78e14:3f574968" + "0" * 56 + ":2"
QMS = "49b9784f8b60426fb3195672b40bd6b7:ed020e40" + "1" * 56 + ":3"
DEADLINE_EVIDENCE = [
    {
        "id": MES,
        "source": "mes-operation-rules.pdf",
        "page_start": 2,
        "page_end": 2,
        "text": "第 3 章 変更の申請\n製造実行システムの変更は、実施日の 5 営業日前までに申請します。",
    },
    {
        "id": QMS,
        "source": "qms-operation-rules.pdf",
        "page_start": 3,
        "page_end": 3,
        "text": "第 4 章 変更の申請\n品質管理システムの変更は、実施日の 3 営業日前までに申請します。",
    },
    {
        "id": "0a1b2c3d4e5f60718293a4b5c6d7e8f9:" + "2" * 64 + ":1",
        "source": "system-ledger.xlsx",
        "page_start": None,
        "page_end": None,
        "text": "システムID: SYS-101 / 正式名: 製造実行システム / 担当部署: 製造部",
    },
]
DEADLINE_ANSWER = "\n\n".join(
    [
        f"製造実行システムの変更は、実施日の 5 営業日前までに申請します【証拠 ID {MES}】。",
        f"品質管理システムの変更は、実施日の 3 営業日前までに申請します【証拠 ID {QMS}】。",
        f"申請の期限は、製造実行システムが 5 営業日前、品質管理システムが 3 営業日前です【証拠 ID {MES}】【証拠 ID {QMS}】。",
        "したがって、申請の期限は製造実行システムの方が長い（5 営業日）です。",
    ]
)


# #1362 の確認の Run run_3b7a1c0ff119477781a0042ea2a8dd23（holdout br-customer-portal-change-window）の形
# （#1412。ID と本文は合成した値）。結論とまとめの段落は根拠の ID を書かず、「根拠」の 2 段落が完全な ID を書く。
PLAN = "815d7759aaaa4887b1b070379e2958af:a657c65a" + "4" * 56
CHANGE = "2a5ec48abbbb4c81989a580a5676476a:58fa1e8c" + "5" * 56
WINDOW_LEDGER = "95e6cc20cccc4339a8e32763c996ce5c:28fb4739" + "6" * 56 + ":28"
WINDOW_EVIDENCE = [
    {
        "id": f"{PLAN}:0",
        "source": "maintenance-plan.pdf",
        "page_start": 1,
        "page_end": 1,
        "text": "定期保守計画 2026年度\n発行: 2026年4月1日 作成: 情シス",
    },
    {
        "id": f"{PLAN}:1",
        "source": "maintenance-plan.pdf",
        "page_start": 1,
        "page_end": 1,
        "text": "第 1 章 定期保守の時間帯\nCustomer Portal: 毎月第 1 日曜日の 0:00〜4:00",
    },
    {
        "id": WINDOW_LEDGER,
        "source": "system-ledger.xlsx",
        "page_start": None,
        "page_end": None,
        "text": "システムID: SYS-128 / 正式名: カスタマーポータル / 略称・別表記: Customer Portal / 重要度: A",
    },
    {
        "id": f"{CHANGE}:4",
        "source": "change-procedure.pdf",
        "page_start": 1,
        "page_end": 1,
        "text": "第 4 章 作業の時間帯\n重要度 A のシステムの変更は、定期保守の時間帯にだけ作業します。",
    },
    {
        "id": f"{CHANGE}:5",
        "source": "change-procedure.pdf",
        "page_start": 1,
        "page_end": 1,
        "text": "第 5 章 利用者への告知\nシステムの停止を伴う変更は、作業の 5 営業日前までに告知します。",
    },
]
WINDOW_ANSWER = (
    "**回答**  \nCustomer Portal（SYS‑128）の本番環境での変更作業は、**毎月第 1 日曜日の 0:00 〜 4:00 の定期保守枠**で"
    "実施できます。\n\n**根拠**  \n"
    f"1. 【定期保守計画 2026年度】「Customer Portal: 毎月第 1 日曜日の 0:00〜4:00」​【evidence_id: {PLAN}:1】  \n"
    f"2. 【システム変更手順書】重要度 A のシステムは「定期保守の時間帯にだけ作業します」​【evidence_id: {CHANGE}:4】\n\n"
    "以上の資料に基づき、Customer Portal の本番変更は上記の保守時間帯に行うことが規定されています。"
)
# 実サービスの最初の判定の形: 結論（A2）は supported で evidence_id が空、まとめ（A6）は citation_error。
WINDOW_FIRST = {
    "A2": ("citation_error", ""),
    "A4": ("supported", f"{PLAN}:1"),
    "A5": ("supported", f"{CHANGE}:4"),
    "A6": ("citation_error", ""),
}


def _cite(*chunk_ids: str) -> str:
    """スタブの evidence_id の書式（chunk の片段の ID を「,」で並べる）。"""
    return ",".join("@" + chunk_id for chunk_id in chunk_ids)


# #1362 の確認の Run run_5352385802954df9a521e1a10ff7d881（dev tl-logistics-oms）の形（#1413。ID と本文は合成した値）。
# 質問は「サンプル物流社のシステム台帳で」と範囲を限定し、根拠には本社の台帳の同じ略称（OMS）の行も入る。
LOGISTICS = "449fb52adddd4843f6b4783146d22de1:c2a2b3d1" + "8" * 56
HEADQUARTERS = "95e6cc20eeee4339a8e32763c996ce5c:28fb4739" + "9" * 56
OMS_EVIDENCE = [
    {
        "id": f"{LOGISTICS}:0",
        "source": "logistics-system-ledger.xlsx",
        "page_start": None,
        "page_end": None,
        "text": "サンプル物流社のシステム台帳（サンプル社の子会社）\n担当部署はサンプル物流社の部署の略号です。",
    },
    {
        "id": f"{LOGISTICS}:1",
        "source": "logistics-system-ledger.xlsx",
        "page_start": None,
        "page_end": None,
        "text": "システムID: SL-201 / 正式名: 運行管理システム / 略称・別表記: OMS / 担当部署: 運",
    },
    {
        "id": f"{HEADQUARTERS}:0",
        "source": "system-ledger.xlsx",
        "page_start": None,
        "page_end": None,
        "text": "サンプル社のシステム台帳\n担当部署は部署の略号（漢字 1 文字）で書きます。",
    },
    {
        "id": f"{HEADQUARTERS}:4",
        "source": "system-ledger.xlsx",
        "page_start": None,
        "page_end": None,
        "text": "システムID: SYS-104 / 正式名: 受発注管理システム / 略称・別表記: OMS、受発注 / 担当部署: 営",
    },
]
OMS_QUESTION = "サンプル物流社のシステム台帳で、略称が OMS のシステムの正式名は何ですか？"


def _oms_answer(name: str) -> str:
    return (
        f"サンプル物流社のシステム台帳で、略称が **OMS** のシステムの正式名は **「{name}」** です。\n\n**根拠**\n"
        "- 「システムID: SL-201 / 正式名: 運行管理システム / 略称・別表記: OMS」"
        f"(logistics-system-ledger.xlsx、シート「システム台帳」行 4)【evidence_id: {LOGISTICS}:1】"
    )


SCOPE_RULE = "その範囲の外の根拠（別の会社の台帳の同じ略称の行など）との違いを contradicted にしない"


LEDGER_ROW = {
    "id": "ledger:23",
    "source": "system-ledger.xlsx",
    "page_start": None,
    "page_end": None,
    "sheet_name": "システム台帳",
    "row_start": 23,
    "row_end": 23,
    "cell_range": "A23:F23",
    "text": "システムID: SYS-119 / 正式名: 購買管理システム / 略称・別表記: PMS / 重要度: A",
}


def test_sheet_locations_are_passed_to_the_model_without_changing_evidence_ids() -> None:
    """表計算の根拠の場所（シート・行・セル範囲）をモデルに渡す根拠の項目に写す（#1450）。

    根拠の ID は本文と範囲から計算するので、場所を足しても変わらない。場所の無い根拠には項目を足さない。
    """
    from rag_engine.evaluation.answer_eval import _evidence_fragments

    [fragment] = list(_evidence_fragments([LEDGER_ROW]))
    assert (fragment["sheet_name"], fragment["row_start"], fragment["row_end"], fragment["cell_range"]) == (
        "システム台帳",
        23,
        23,
        "A23:F23",
    )
    bare = {key: value for key, value in LEDGER_ROW.items() if key not in {"sheet_name", "row_start", "row_end", "cell_range"}}
    [without] = list(_evidence_fragments([bare]))
    assert without["evidence_id"] == fragment["evidence_id"]
    assert "sheet_name" not in without and "section_path" not in without
    [pdf] = list(_evidence_fragments(EVIDENCE))
    assert set(pdf) == {
        "id",
        "source",
        "source_run_id",
        "page_start",
        "page_end",
        "parent_id",
        "text",
        "fragment_start",
        "fragment_end",
        "evidence_id",
    }


# 段落の分け方の事例（RAG と Agent の両方のテストが読む。#1455）。
SENTENCE_CASES = json.loads(
    (Path(__file__).resolve().parents[4] / "platform/contracts/answer-passages/sentence-splits.json").read_text("utf-8")
)


@pytest.mark.parametrize("case", SENTENCE_CASES["cases"], ids=lambda case: case["id"])
def test_passages_are_not_split_inside_brackets(case: dict) -> None:
    """括弧の中の「。」で段落を分けず、太字の印・閉じ括弧だけの断片を作らない（#1455）。"""
    from rag_engine.generation.operation_audit import answer_passages

    assert [wire_text(item) for item in answer_passages(case["line"])] == case["passages"]


def test_unverified_section_titles_match_the_builtin_language_pack() -> None:
    """回答の末尾の節（回答に入れられなかった範囲・省いた処理）の見出しは、共有の語彙
    （platform/contracts/answer-passages/vocabulary.json の unverified_section_titles）と同じ（#3240。
    Agent は RAG の言語資源を読まず、契約の語彙でこの節を見分ける）。"""
    from rag_parser_core.language import builtin_pack

    vocabulary = json.loads((Path(__file__).resolve().parents[4] / "platform/contracts/answer-passages/vocabulary.json").read_text("utf-8"))
    pack = builtin_pack()
    titles = [pack.text("answer_labels", key) for key in ("gaps_section", "unverified_section", "deadline_section")]

    assert vocabulary["unverified_section_titles"] == titles


def test_passage_ranges_locate_each_passage_in_the_answer() -> None:
    """段落の位置（``answer_passage_ranges``）は、回答の中のその段落の原文を指す（#3240。MCP の
    ``rag_validate_answer`` の ``answer_range``）。複数の行・行頭の空白・空の行・600 文字を超える文を含む。"""
    from rag_engine.generation.operation_audit import answer_passage_ranges, answer_passages

    long_sentence = "あ" * 650 + "。"
    answer = "\n".join(
        [
            *(case["line"] for case in SENTENCE_CASES["cases"]),
            "",
            "   見出し",
            f"  {long_sentence}続きの文です。",
            "  「引用の文です。引用の続きです。」",
        ]
    )
    passages = answer_passages(answer)
    ranges = answer_passage_ranges(answer)

    assert [passage_id for passage_id, _start, _end in ranges] == [item["id"] for item in passages]
    assert [answer[start:end] for _id, start, end in ranges] == [wire_text(item) for item in passages]
    assert [answer[start:end] for _id, start, end in ranges][-4:] == [
        "あ" * 600,
        "あ" * 50 + "。",
        "続きの文です。",
        "「引用の文です。引用の続きです。」",
    ]


PREMISE_ANSWER = (
    "利用者の回答により、部門長の承認は得ていません。\n\n"
    "承認が得られていないため、アカウントは削除できません。\n\n"
    "削除の承認は人事部長が行います。"
)
PREMISE_EVIDENCE = [
    {
        "id": "c1",
        "source": "manual.pdf",
        "page_start": 2,
        "page_end": 2,
        "text": "削除の前に、所属部署の部門長の承認を得てください。承認の無い削除は行いません。",
    }
]


SCOPE_ANSWER = "共有グループの表示区分を変更し、保存して確定します。\n\n項目 A の所属先だけを変更します。"
SCOPE_EVIDENCE = [
    {
        "id": "c1",
        "source": "manual.pdf",
        "page_start": 3,
        "page_end": 3,
        "text": "共有グループの表示区分を変更すると、グループのすべての項目に反映されます。保存して確定します。",
    }
]


DATA_ANSWER = "部長の承認が要る範囲の受注は 6 件です。\n\n受注金額は 6 万〜24 万円なので、6 件すべてが部長の承認の範囲に入ります。"
DATA_EVIDENCE = [{"id": "c1", "source": "rules.pdf", "page_start": 1, "page_end": 1, "text": "5 万円以上 30 万円未満は部長が承認する。"}]


QUESTION_TEXT = "年収 120 万円の配偶者は、収入の要件を満たしますか。"
QUESTION_ANSWER = (
    "配偶者の年収は 120 万円です。\n\n年収 120 万円は 130 万円未満なので、収入の要件を満たします。\n\n要件の確認は年に 2 回行います。"
)
QUESTION_EVIDENCE = [
    {"id": "c1", "source": "rules.pdf", "page_start": 1, "page_end": 1, "text": "収入の要件は年収 130 万円未満であること。"},
]


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("set0123456789abcdef:37", True),
        ("set0123…:37", True),
        ("set0123456789abcdef:38", False),
        ("37", False),
        ("set:37", False),
    ],
)
def test_chunk_ids_written_without_the_document_id_point_to_their_evidence(written: str, expected: bool) -> None:
    """先頭の文書の id を落とした「chunk_set:番号」の ID は、後ろの部分にそろえて照合する（番号はそのまま一致）。"""
    from rag_engine.evaluation.answer_eval import _abbreviated_id_matches

    assert _abbreviated_id_matches(written, "doc0123456789:set0123456789abcdef:37") is expected


# ---- 監査の判定の根拠の ID の照合（#2622）。設定の名前・本文・ID はすべて合成した値。----
SETTING_SET = "0f1e2d3c4b5a69788796a5b4c3d2e1f0:" + "a1" * 32
SETTING_OFF = f"{SETTING_SET}:1"
SETTING_NOTE = f"{SETTING_SET}:2"
SETTING_EVIDENCE = [
    {"id": SETTING_OFF, "source": "synthetic-settings.pdf", "page_start": 1, "page_end": 1, "text": "設定Aは無効です。"},
    {"id": SETTING_NOTE, "source": "synthetic-settings.pdf", "page_start": 1, "page_end": 1, "text": "設定Bは毎日 0 時に実行します。"},
]
SETTING_ANSWER = "設定Aは有効です。"
# catalog に無い片段の ID（監査モデルが返した値の形）。
UNKNOWN_SPAN_ID = "E123456789abcdef"


def _setting_catalog() -> dict[str, dict]:
    from rag_engine.evaluation.answer_eval import _evidence_fragments

    return {item["evidence_id"]: item for item in _evidence_fragments(SETTING_EVIDENCE)}


def _span_id(chunk_id: str) -> str:
    return next(item["evidence_id"] for item in _setting_catalog().values() if item["id"] == chunk_id)


REFERENCE_CASES = [
    # （名前, 段落, 監査の evidence_id, 監査の引用, reference_check）。
    ("正常な ID", SETTING_ANSWER, "{off}", "", "resolved"),
    ("監査の ID だけが無効で、回答の ID で照合", f"設定Aは有効です【証拠 ID {SETTING_OFF}】。", UNKNOWN_SPAN_ID, "", "resolved_by_answer"),
    ("回答側の無効な ID", f"設定Aは有効です【証拠 ID {SETTING_SET}:9】。", "", "", "answer_id_invalid"),
    ("回答側の無効な ID（監査の ID も無効）", f"設定Aは有効です【証拠 ID {SETTING_SET}:9】。", UNKNOWN_SPAN_ID, "", "answer_id_invalid"),
    ("監査モデルだけの無効な ID", SETTING_ANSWER, UNKNOWN_SPAN_ID, "", "audit_id_invalid"),
    ("監査モデルだけの ID でない値", SETTING_ANSWER, "第 3 章", "", "audit_id_invalid"),
    ("曖昧な省略 ID", SETTING_ANSWER, f"{SETTING_SET[:40]}…", "", "ambiguous_id"),
    ("ID の欠落", SETTING_ANSWER, "", "", "audit_id_missing"),
    ("引用が根拠に無い", SETTING_ANSWER, "{off}, {note}", "根拠に無い合成の文", "quote_unmatched"),
]


# ---- 非主張の分類の食い違いの監査し直し（#2625） ----------------------------------------------------------
# 同じ内容（合成の「上限は最長 90 日」）を、文・箇条書き・見出しの記法・表の行・例示の形で書いた段落。
LIMIT_EVIDENCE = [{"id": "c1", "source": "manual-a.pdf", "page_start": 1, "page_end": 1, "text": "延長の上限は最長 90 日です。"}]
LIMIT_FORMS = {
    "sentence": "延長の上限は最長 90 日です。",
    "bullet": "- 延長の上限は最長 90 日です。",
    "bold": "**延長の上限は最長 90 日**",
    "markdown_heading": "## 延長の上限は最長 90 日",
    "table_row": "| 延長の上限 | 最長 90 日 |",
    "example": "例: 30 日を超えて延長するときは、最長 90 日まで延長できます。",
}
LIMIT_OWNER = "申請は所属の管理者が行います。"


def _limit_answer(form: str) -> str:
    text = LIMIT_FORMS[form]
    if form == "table_row":
        return f"| 項目 | 内容 |\n| --- | --- |\n{text}"
    return f"{text}\n\n{LIMIT_OWNER}"


# ---- 図の見た目だけで決まる主張（#2678）。合成の判定フロー図の根拠（OCR の文字に矢印・配置が無い）。----

FLOW_TEXT = {"id": "t1", "source": "manual.pdf", "page_start": 1, "page_end": 1, "text": "申請は判定の後に処理する。"}
FLOW_FIGURE = {
    "id": "f1",
    "source": "manual.pdf",
    "page_start": 2,
    "page_end": 2,
    "evidence_type": "ocr",
    "text": "開始 判定 処理A 処理B 終了",
}
FLOW_ANSWER = (
    "判定の後に処理します。\n根拠：manual.pdf p.1\n\n"
    "判定の右の分岐は処理Aにつながります。\n根拠：manual.pdf p.2\n\n"
    "処理Bは左の分岐に属します。\n根拠：manual.pdf p.2"
)


@pytest.mark.parametrize(
    ("quote", "text"),
    [
        ("値は **43** です。", "値は 42 です。"),
        ("対象の状態を確認します。", "別の記録を保存します。"),
        ("式は 23 です。", "式は 2*3 です。"),
        ("値は 23 です。", "値は 2**3 です。"),
    ],
)
def test_quote_format_normalization_does_not_accept_changed_content(quote: str, text: str) -> None:
    from rag_engine.generation.grounded import quote_in_text

    assert not quote_in_text(quote, text)
