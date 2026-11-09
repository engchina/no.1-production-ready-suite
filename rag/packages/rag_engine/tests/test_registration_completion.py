"""#368: 表の後に続く番号付き操作を本文予算で欠落させない。"""

from rag_engine.retrieval.evidence_selection import evidence_excerpt

QUESTION = "新しい配送業者の業者コードが配送業者登録画面の一覧に見当たらない。どうすればよいか。"
STEPS = [
    "１. 業者の基本情報を入力します。",
    "２. 対応できる配送区分を選択します。",
    "３. 備考を入力します。",
    "４. 実行ボタンを押して登録を完了します。",
]


def test_unrelated_numbered_labels_are_not_mandatory_steps():
    text = "１. 業者コードの例\n" + "説明\n" * 200 + "目的の定義。"
    excerpt = evidence_excerpt("目的の定義は？", text, 40)
    assert "目的の定義" in excerpt
