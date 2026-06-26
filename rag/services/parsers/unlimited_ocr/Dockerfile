# syntax=docker/dockerfile:1
# build context = リポジトリ root。
# Unlimited-OCR は公式 SGLang wheel を使うため、SGLang の CUDA image をベースにし、
# 同一コンテナ内で SGLang server と parser FastAPI を起動する。
FROM lmsysorg/sglang:v0.5.14

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/root/.cache/huggingface \
    UNLIMITED_OCR_RUNTIME=sglang \
    UNLIMITED_OCR_SGLANG_BASE_URL=http://127.0.0.1:10000/v1 \
    UNLIMITED_OCR_SGLANG_MODEL=Unlimited-OCR

RUN python3 -m pip install --no-cache-dir --force-reinstall --no-deps \
    "https://huggingface.co/baidu/Unlimited-OCR/resolve/main/wheel/sglang-0.0.0.dev11416+g92e8bb79e-py3-none-any.whl" \
    && python3 -m pip install --no-cache-dir \
    "kernels==0.11.7" \
    "pymupdf==1.27.2.2"

COPY packages/rag_parser_core /opt/rag_parser_core
RUN pip install --no-cache-dir "/opt/rag_parser_core[service]"

WORKDIR /app
COPY services/parsers/unlimited_ocr/app ./app
COPY services/parsers/unlimited_ocr/entrypoint.sh /usr/local/bin/unlimited-ocr-entrypoint.sh
RUN chmod +x /usr/local/bin/unlimited-ocr-entrypoint.sh

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"
ENTRYPOINT ["/usr/local/bin/unlimited-ocr-entrypoint.sh"]
