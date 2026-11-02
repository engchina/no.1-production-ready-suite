"""Docling が存在だけで再利用する RapidOCR ファイルを SHA-256 で検証する。"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest
from docling.models.stages.ocr.rapid_ocr_model import RapidOcrModel
from rag_engine.resources.docling_models import verify_rapidocr_cache


def test_existing_invalid_download_is_detected_before_model_loading(tmp_path: Path) -> None:
    # 実 Docling の取得経路を使い、通信だけを途中で切れた合成内容へ置き換える。
    target = "docling.models.stages.ocr.rapid_ocr_model.download_url_with_progress"
    with patch(target, side_effect=lambda *args, **kwargs: io.BytesIO(b"incomplete")) as download:
        RapidOcrModel.download_models("onnxruntime", local_dir=tmp_path)
        assert download.call_count > 0
    with patch(target) as download:
        RapidOcrModel.download_models("onnxruntime", local_dir=tmp_path)
        download.assert_not_called()
    with pytest.raises(RuntimeError, match="ハッシュ"):
        verify_rapidocr_cache(tmp_path, repair=False)
