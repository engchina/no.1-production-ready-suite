"""画像補正(image_enhance)前処理マイクロサービス。

共有 contract(rag_parser_core)の app factory を使い、スキャン画像・写真を OCR 向けに
補正(グレースケール/ノイズ除去/コントラスト均一化/deskew)して `ConvertResponse` を返す。
重い CV 依存(OpenCV)はこのサービスに隔離し、他 parser / backend に非干渉。
"""

from rag_parser_core.preprocess import ConvertHealth, supported_profiles_from
from rag_parser_core.preprocess_service import create_preprocess_app

from app.converters import convert


def _health() -> ConvertHealth:
    """OpenCV が import できれば ready、無ければ degraded。"""
    try:
        import cv2  # noqa: F401

        status = "ok"
        version: str | None = getattr(__import__("cv2"), "__version__", None)
    except Exception:  # noqa: BLE001 - 依存欠如は degraded として可視化する
        status = "degraded"
        version = None
    return ConvertHealth(
        status=status,
        backend="preprocess-image-enhance",
        package_name="opencv-python-headless",
        package_version=version,
        supported_profiles=supported_profiles_from(["image_enhance"]),
    )


app = create_preprocess_app(
    converter=convert, health_probe=_health, title="preprocess-image-enhance"
)
