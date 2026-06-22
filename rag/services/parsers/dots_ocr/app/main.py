"""Dots.OCR(GPU)parser マイクロサービス。

CUDA イメージ上で Dots.OCR の実 OCR を行い、共通 contract の `StructuredExtraction`
を返す。GPU 依存は本 image に隔離され、他 parser / backend に影響しない。
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


def _vllm_runtime_ready() -> bool:
    """vLLM runtime 選択時は sidecar の /health まで確認する。"""
    runtime = os.environ.get("DOTS_OCR_RUNTIME", "vllm").strip().lower() or "vllm"
    if runtime not in {"vllm", "official_vllm"}:
        return _cuda_ready()
    protocol = os.environ.get("DOTS_OCR_PROTOCOL", "http").strip() or "http"
    host = os.environ.get("DOTS_OCR_IP", "parser-dots-ocr-vllm").strip()
    port = os.environ.get("DOTS_OCR_PORT", "8000").strip() or "8000"
    timeout = float(os.environ.get("DOTS_OCR_HEALTH_TIMEOUT_SECONDS", "3"))
    return _http_health_ready(f"{protocol}://{host}:{port}/health", timeout)


def _cuda_ready() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001 - readiness 境界では False へ正規化する
        return False


app = create_parse_app(
    backend="dots_ocr",
    import_name="dots_ocr",
    distribution_names=("dots-ocr", "dots_ocr"),
    runtime_health=_vllm_runtime_ready,
    title="parser-dots-ocr",
)
