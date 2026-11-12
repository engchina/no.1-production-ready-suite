"""発話の分類と、過去の短い応答の記録に表示する名前。"""

# 分類の予測は表示だけに使い、検索を省く条件にはしない。
SMALL_TALK_REASONS: dict[str, str] = {
    "greeting": "挨拶・お礼",
    "small_talk": "雑談",
}
