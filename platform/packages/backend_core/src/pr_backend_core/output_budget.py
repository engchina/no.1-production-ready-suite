"""オンライン生成の出力予算。固定した小課題・ファイル解析は呼び出し元の契約を使う。"""

from __future__ import annotations

OUTPUT_TOKEN_TIERS = (16_384, 32_768, 65_536)


def maximum_online_output_budget(capacity: int) -> int:
    """オンラインで実際に要求し得る上限だけを入力のために予約する。"""
    return max(0, min(capacity, max(OUTPUT_TOKEN_TIERS)))


def initial_output_budget(capacity: int, *, room: int | None = None) -> int:
    """初回だけ容量・入力余量で小さくできる。登録値を引き上げない。"""
    limit = min(capacity, room if room is not None else capacity)
    if limit <= 0:
        raise ValueError("入力と出力のための token 予算が残っていません。")
    return min(OUTPUT_TOKEN_TIERS[0], limit)


def next_output_budget(current: int, capacity: int, *, room: int | None = None) -> int | None:
    """次の段階が収まる場合だけ返す。中間値・同じ上限での再送は作らない。"""
    next_tier = next((value for value in OUTPUT_TOKEN_TIERS if value > current), None)
    limit = min(capacity, room if room is not None else capacity)
    return next_tier if next_tier is not None and next_tier <= limit else None


def output_limit_message(*, model: str, attempts: int, capacity: int) -> str:
    """容量や全体予算の制約の中で終わらなかったことを、利用者へ明示する。"""
    return (
        f"モデル「{model}」の回答が出力の上限に達し、{attempts}回の試行で完了できませんでした。"
        f"登録された出力容量（{capacity} tokens）と全体の処理予算の範囲で停止しました。"
        "質問を分けるか対象を絞って、もう一度お試しください。"
    )
