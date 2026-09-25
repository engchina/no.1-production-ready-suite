"""JSON 構造化ロギング設定（サービス横断で共通）。"""

import logging
from collections.abc import Mapping

from pythonjsonlogger import json as jsonlogger


def configure_logging(
    level: str = "INFO",
    *,
    quiet_loggers: Mapping[str, int] | None = None,
) -> None:
    """ルートロガーを JSON 形式で構成する。

    Args:
        level: ルートロガーのレベル。
        quiet_loggers: 過剰な警告を出すサードパーティロガーのレベル引き上げ指定
            （例: {"pdfminer": logging.ERROR}）。サービス固有のノイズはここで渡す。
    """
    handler = logging.StreamHandler()
    handler.setFormatter(
        jsonlogger.JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            rename_fields={"asctime": "timestamp", "levelname": "level"},
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    for name, logger_level in (quiet_loggers or {}).items():
        logging.getLogger(name).setLevel(logger_level)
