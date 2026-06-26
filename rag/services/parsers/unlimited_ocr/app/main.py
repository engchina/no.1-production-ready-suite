"""Unlimited-OCR(GPU)parser マイクロサービス。

既定では同一コンテナ内 SGLang の OpenAI-compatible endpoint へ委譲し、共通 contract の
`StructuredExtraction` を返す。`UNLIMITED_OCR_RUNTIME=transformers` の時だけ旧
transformers 直ロードへ退避する。
"""

import os
import socket
from urllib.parse import urlparse

from rag_parser_core.service import create_parse_app


def _http_health_ready(url: str, timeout: float) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/health"
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            request = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                "Connection: close\r\n\r\n"
            )
            sock.sendall(request.encode("ascii"))
            response = sock.recv(64)
    except OSError:
        return False
    return response.startswith(b"HTTP/1.1 2") or response.startswith(b"HTTP/1.0 2")


def _runtime_ready() -> bool:
    runtime = os.environ.get("UNLIMITED_OCR_RUNTIME", "sglang").strip().lower() or "sglang"
    if runtime not in {"sglang", "official_sglang"}:
        return _cuda_ready()
    base_url = os.environ.get(
        "UNLIMITED_OCR_SGLANG_BASE_URL",
        "http://127.0.0.1:10000/v1",
    ).strip()
    root_url = base_url.rstrip("/")
    if root_url.endswith("/v1"):
        root_url = root_url[:-3]
    timeout = float(os.environ.get("UNLIMITED_OCR_HEALTH_TIMEOUT_SECONDS", "3"))
    return _http_health_ready(f"{root_url}/health", timeout)


def _cuda_ready() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001 - readiness 境界では False へ正規化する
        return False


app = create_parse_app(
    backend="unlimited_ocr",
    import_name="sglang",
    distribution_names=("sglang",),
    runtime_health=_runtime_ready,
    title="parser-unlimited-ocr",
)
