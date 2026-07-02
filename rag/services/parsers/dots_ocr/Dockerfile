# syntax=docker/dockerfile:1
# build context = リポジトリ root。
# Dots.OCR は公式 self-host(vLLM OpenAI 互換)で配信する。公式 vLLM イメージ
# (CUDA + torch + vLLM + transformers 同梱。vLLM 0.11+ で Dots.OCR を公式サポート)を
# ベースにし、**同一コンテナ内**で vLLM サーバと parser FastAPI を起動する。これにより
# 従来分離していた `parser-dots-ocr` と `parser-dots-ocr-vllm` の 2 イメージを 1 つへ統合する。
FROM vllm/vllm-openai:v0.22.1

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/root/.cache/huggingface \
    # parser → 同一コンテナ内の vLLM(localhost:8080)を指す。
    DOTS_OCR_RUNTIME=vllm \
    DOTS_OCR_PROTOCOL=http \
    DOTS_OCR_IP=127.0.0.1 \
    DOTS_OCR_PORT=8080 \
    DOTS_OCR_MODEL_NAME=model

# dots.ocr の GitHub install と画像処理(PyMuPDF/opencv)に必要な system 依存。
RUN apt-get update \
    && apt-get install -y --no-install-recommends git libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /root/.cache

# parser app の HTTP サーバ依存(FastAPI/gunicorn 等)を base の Python へ追加する。
COPY packages/rag_parser_core /opt/rag_parser_core
RUN pip install --no-cache-dir "/opt/rag_parser_core[service]"

# Dots.OCR 本体は GitHub 配布。requirements が transformers==4.56.1 を hard pin しており、
# そのまま入れると base(vLLM 用の新しい transformers)を破壊するため --no-deps で導入し、
# vLLM 経路に必要な軽量 runtime 依存だけを別途入れる(torch/transformers/vLLM は base を流用)。
# ponytail: vLLM 経路の最小依存に絞る。HF fallback(DOTS_OCR_RUNTIME=hf)で import 不足が出たら
#           当該 package を追記する(torch/transformers は base 同梱)。
RUN git clone --depth 1 https://github.com/rednote-hilab/dots.ocr.git /opt/dots.ocr \
    && pip install --no-cache-dir --no-deps /opt/dots.ocr \
    && pip install --no-cache-dir openai qwen_vl_utils PyMuPDF huggingface_hub accelerate cairosvg \
    # 公式 install 手順と同様、model/ 配下の custom code を installed package へ同梱する。
    && pkg="$(python3 -c 'import importlib.util, pathlib; print(pathlib.Path(importlib.util.find_spec("dots_ocr").origin).parent)')" \
    && mkdir -p "$pkg/model" \
    && cp -R /opt/dots.ocr/dots_ocr/model/. "$pkg/model"/ \
    && rm -rf /opt/dots.ocr

# モデルをビルド時に取得してイメージへ焼き込む(prod・配布向け。実行時 DL を排除)。
# dev は `--build-arg BAKE_MODELS=0` で省き named volume のキャッシュを使う。
# 別モデルは `--build-arg DOTS_OCR_MODEL=...`、ミラーは HF_ENDPOINT build-arg、token は runtime env のみ(image 履歴へ残さない)。
ARG DOTS_OCR_MODEL=rednote-hilab/dots.mocr
ENV DOTS_OCR_MODEL=${DOTS_OCR_MODEL}
ARG BAKE_MODELS=1
ARG HF_ENDPOINT=
RUN if [ "$BAKE_MODELS" = "1" ]; then \
        HF_ENDPOINT="$HF_ENDPOINT" \
        python3 -c "from huggingface_hub import snapshot_download; snapshot_download('${DOTS_OCR_MODEL}')"; \
    fi

WORKDIR /app
COPY services/parsers/dots_ocr/app ./app
COPY services/parsers/dots_ocr/entrypoint.sh /usr/local/bin/dots-ocr-entrypoint.sh
RUN chmod +x /usr/local/bin/dots-ocr-entrypoint.sh

EXPOSE 8000
# parser :8000/health のみ確認する。vLLM の起動状況は parser の runtime_health が別フィールド
# として反映する。モデルロードが遅いため start-period を長めに取る。
HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"
ENTRYPOINT ["/usr/local/bin/dots-ocr-entrypoint.sh"]
