"""GLM-OCR(GPU)parser マイクロサービス。

CUDA イメージ上で HuggingFace の GLM-OCR(既定 zai-org/GLM-OCR)を transformers で
ロードして実 OCR を行い、共通 contract の `StructuredExtraction` を返す。GPU/重い ML
依存は本 image に隔離され、他 parser / backend に影響しない。

実 OCR の呼び出しは `rag_parser_core.registry._run_glm_ocr`。専用 pip package が無いため
readiness の version 検出は transformers を代理に使う。
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
    """vLLM runtime 選択時は OpenAI-compatible sidecar の /health まで確認する。"""
    runtime = os.environ.get("GLM_OCR_RUNTIME", "vllm").strip().lower() or "vllm"
    if runtime not in {"vllm", "official_vllm"}:
        return _cuda_ready()
    base_url = os.environ.get(
        "GLM_OCR_VLLM_BASE_URL", "http://parser-glm-ocr-vllm:8080/v1"
    ).strip()
    root_url = base_url.rstrip("/")
    if root_url.endswith("/v1"):
        root_url = root_url[:-3]
    timeout = float(os.environ.get("GLM_OCR_HEALTH_TIMEOUT_SECONDS", "3"))
    return _http_health_ready(f"{root_url}/health", timeout)


def _cuda_ready() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001 - readiness 境界では False へ正規化する
        return False


app = create_parse_app(
    backend="glm_ocr",
    import_name="transformers",
    distribution_names=("transformers",),
    runtime_health=_vllm_runtime_ready,
    title="parser-glm-ocr",
)
