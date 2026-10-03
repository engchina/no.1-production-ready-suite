"""JSON 構造化ロギング設定。

実装は共有 backend インフラ `pr_backend_core` に移管。RAG 固有のノイズロガー抑制
（pdfminer のフォント警告等）をここで注入する。
"""

import logging
import re
from urllib.parse import urlsplit

from pr_backend_core import configure_logging as _configure_logging

# PDF パース(pdfminer / pdfplumber 経由)が出す無害な警告でログが溢れるため抑制する。
# 例: "Could not get FontBBox from font descriptor because None cannot be parsed as 4 floats"
# これらはフォント記述子の欠損に対する fallback で、抽出結果には影響しない。
_NOISY_LOGGERS: dict[str, int] = {
    "httpx": logging.WARNING,
    "pdfminer": logging.ERROR,
    "pdfminer.pdffont": logging.ERROR,
    "pdfminer.pdfinterp": logging.ERROR,
}


class _ServiceStatusAccessFilter(logging.Filter):
    """サービス状態ポーリングの access log だけを落とす。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) == 5:
            _, method, path, _, status = record.args
        else:
            # 旧 server のレコードにも対応するが、status を解釈できない行は落とさない。
            match = re.search(r'"(\w+) ([^ ]+) HTTP/[^" ]+" (\d{3})(?:\s|$)', record.getMessage())
            if not match:
                return True
            method, path, raw_status = match.groups()
            status = int(raw_status)
        if not isinstance(path, str) or not isinstance(status, int):
            return True
        route = urlsplit(path).path
        return not (
            method == "GET"
            and route.startswith("/api/services/")
            and route.endswith("/status")
            and 200 <= status < 300
        )


def _install_uvicorn_access_filters() -> None:
    access_logger = logging.getLogger("uvicorn.access")
    if any(isinstance(item, _ServiceStatusAccessFilter) for item in access_logger.filters):
        return
    access_logger.addFilter(_ServiceStatusAccessFilter())


def configure_logging(level: str = "INFO", *, component: str = "api") -> None:
    """ルートロガーを JSON 形式で構成する（共有実装 + RAG 固有のノイズ抑制）。"""
    from app.config import get_settings

    settings = get_settings()
    _configure_logging(
        level,
        quiet_loggers=_NOISY_LOGGERS,
        service_name=settings.service_name,
        service_version=settings.app_version,
        environment=settings.environment,
        component=component,
    )
    _install_uvicorn_access_filters()
