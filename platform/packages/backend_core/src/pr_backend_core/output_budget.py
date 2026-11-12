"""オンライン生成の単一出力予算。固定課題と文書解析は呼出元の契約を使う。"""

from __future__ import annotations

DEFAULT_ONLINE_MAX_OUTPUT_TOKENS = 65_536


def configured_online_output_tokens(settings: object) -> int:
    """3製品で共通の設定値を取得する。モデルの能力登録とは分ける。"""
    value = int(getattr(settings, "online_max_output_tokens", DEFAULT_ONLINE_MAX_OUTPUT_TOKENS))
    if value <= 0:
        raise ValueError("オンライン生成の出力予算は正の整数で指定してください。")
    return value


def online_output_budget(
    capacity: int,
    *,
    configured: int = DEFAULT_ONLINE_MAX_OUTPUT_TOKENS,
    room: int | None = None,
    total_room: int | None = None,
) -> int:
    """設定・登録容量・context・全体予算に収まる1回の要求。余量がなければ送らない。"""
    limits = [capacity, configured]
    limits.extend(value for value in (room, total_room) if value is not None)
    budget = min(limits)
    if budget <= 0:
        raise ValueError("入力と出力のための token 予算が残っていません。")
    return budget


def output_limit_message(*, model: str, attempts: int = 1, capacity: int) -> str:
    """出力の上限で中断した回答を成功にせず、次に取れる操作を示す。"""
    return (
        f"モデル「{model}」の回答が出力の上限に達したため、完了できませんでした。"
        "途中の回答は確定していません。"
        "質問を分けるか対象を絞って、もう一度お試しください。"
    )
