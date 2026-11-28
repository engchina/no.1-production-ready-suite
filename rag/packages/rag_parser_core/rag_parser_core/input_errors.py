"""解析の失敗が入力のファイル自体の不正(破損・偽装拡張子・暗号化)によるものかの判定。"""

from __future__ import annotations

# 「入力ファイル自体が不正(破損・偽装拡張子・暗号化)」を示す例外の型名。
# core は pypdfium2 / PIL 等の adapter 依存を import できない(adapter 未導入環境でも
# registry は import される)ため、型そのものではなく型名でマッチする。
_INVALID_INPUT_EXCEPTION_NAMES = frozenset(
    {
        "PdfiumError",  # pypdfium2: "Data format error" 等
        "PDFSyntaxError",  # pdfminer(unstructured)
        "PSEOF",
        "PSSyntaxError",
        "UnidentifiedImageError",  # PIL(画像系 OCR adapter)
        "BadZipFile",  # docx/pptx/xlsx(unstructured/docling)
        "FileDataError",  # dots.ocr の公式 parser が内部で PDF を開けないとき
        "EmptyFileError",
    }
)
_INVALID_INPUT_MESSAGE_MARKERS = (
    "data format error",
    "cannot open broken document",
    "not a pdf",
    "cannot identify image",
    "file has not been decrypted",
    "password",
)


def is_invalid_input_error(exc: BaseException) -> bool:
    """例外連鎖を辿り、入力データ不正(破損ファイル等)由来かを判定する。

    adapter の実行(``run_external_adapter``)と、解析サービスが自分で解析する経路
    (``service_failure_warning``)の両方がこの 1 か所の判定を使う(#3217)。
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if type(current).__name__ in _INVALID_INPUT_EXCEPTION_NAMES:
            return True
        message = str(current).casefold()
        if any(marker in message for marker in _INVALID_INPUT_MESSAGE_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False
