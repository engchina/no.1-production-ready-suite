"""JSON 構造化ロギング設定。"""

import logging

from pythonjsonlogger import json as jsonlogger


def configure_logging(level: str = "INFO") -> None:
    """ルートロガーを JSON 形式で構成する。"""
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
