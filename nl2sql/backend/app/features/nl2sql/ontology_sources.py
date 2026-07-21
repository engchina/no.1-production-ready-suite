"""Ontology 構築資料の安全な保存と決定論的な多形式抽出。"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import importlib
import io
import json
import mimetypes
import unicodedata
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import UploadFile

from app.clients.oci_auth import load_oci_config_without_prompt
from app.settings import Settings, get_settings

from .ontology_models import (
    OntologyEvidence,
    OntologyEvidenceLocatorKind,
    OntologySourceDocument,
    QaPair,
)

SUPPORTED_ONTOLOGY_SOURCE_SUFFIXES = frozenset(
    {".pdf", ".docx", ".txt", ".md", ".csv", ".tsv", ".xlsx", ".xlsm"}
)
_CHUNK_SIZE = 1024 * 1024
_ALLOWED_MEDIA_TYPES: dict[str, frozenset[str]] = {
    ".pdf": frozenset({"application/pdf"}),
    ".docx": frozenset({"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}),
    ".txt": frozenset({"text/plain"}),
    ".md": frozenset({"text/markdown", "text/plain"}),
    ".csv": frozenset({"text/csv", "application/csv", "text/plain"}),
    ".tsv": frozenset({"text/tab-separated-values", "text/plain"}),
    ".xlsx": frozenset({"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}),
    ".xlsm": frozenset({"application/vnd.ms-excel.sheet.macroenabled.12"}),
}


class OntologySourceError(ValueError):
    def __init__(self, code: str, message_ja: str) -> None:
        super().__init__(message_ja)
        self.code = code
        self.message_ja = message_ja


@dataclass(frozen=True)
class ExtractedSourceChunk:
    text: str
    locator_kind: OntologyEvidenceLocatorKind
    locator: str

    def evidence(self, source: OntologySourceDocument) -> OntologyEvidence:
        normalized = normalize_source_text(self.text)
        return OntologyEvidence(
            source_document_id=source.id,
            source_sha256=source.sha256,
            locator_kind=self.locator_kind,
            locator=self.locator,
            excerpt_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            excerpt_ja=normalized[:500],
        )


@dataclass(frozen=True)
class ExtractedOntologySource:
    chunks: list[ExtractedSourceChunk]
    qa_pairs: list[QaPair]
    warnings_ja: list[str]

    @property
    def business_text(self) -> str:
        return "\n\n".join(chunk.text for chunk in self.chunks if chunk.text.strip())


def normalize_source_text(value: str) -> str:
    lines = [" ".join(line.split()) for line in unicodedata.normalize("NFC", value).splitlines()]
    return "\n".join(line for line in lines if line).strip()


def decode_source_text(content: bytes) -> str:
    """日本語資料で一般的な encoding を固定順で判定し、置換文字を混入させない。"""

    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return content.decode("utf-16")
        except UnicodeDecodeError as exc:
            raise OntologySourceError(
                "ONTOLOGY_SOURCE_ENCODING_INVALID", "資料の文字コードを判定できません。"
            ) from exc
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise OntologySourceError(
        "ONTOLOGY_SOURCE_ENCODING_INVALID", "資料の文字コードを判定できません。"
    )


def validate_source_signature(filename: str, content: bytes) -> None:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_ONTOLOGY_SOURCE_SUFFIXES:
        raise OntologySourceError(
            "ONTOLOGY_SOURCE_FORMAT_UNSUPPORTED",
            "PDF、DOCX、TXT、MD、CSV、TSV、XLSX、XLSM のいずれかを指定してください。",
        )
    if not content:
        raise OntologySourceError("ONTOLOGY_SOURCE_EMPTY", "空の資料は登録できません。")
    if suffix == ".pdf" and not content.startswith(b"%PDF-"):
        raise OntologySourceError("ONTOLOGY_SOURCE_MIME_MISMATCH", "PDF の内容を確認できません。")
    if suffix in {".docx", ".xlsx", ".xlsm"}:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                names = frozenset(archive.namelist())
        except (OSError, zipfile.BadZipFile) as exc:
            raise OntologySourceError(
                "ONTOLOGY_SOURCE_ARCHIVE_INVALID", "Office ファイルが壊れています。"
            ) from exc
        expected = "word/document.xml" if suffix == ".docx" else "xl/workbook.xml"
        if expected not in names:
            raise OntologySourceError(
                "ONTOLOGY_SOURCE_MIME_MISMATCH", "拡張子と Office ファイル内容が一致しません。"
            )
    if suffix in {".txt", ".md", ".csv", ".tsv"} and b"\x00" in content[:4096]:
        raise OntologySourceError(
            "ONTOLOGY_SOURCE_BINARY_TEXT", "テキスト資料にバイナリデータが含まれています。"
        )


def validate_source_path(filename: str, path: Path) -> None:
    """API のメモリへ原本全体を戻さず、保存済みファイルの magic bytes を検査する。"""

    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_ONTOLOGY_SOURCE_SUFFIXES:
        raise OntologySourceError(
            "ONTOLOGY_SOURCE_FORMAT_UNSUPPORTED",
            "PDF、DOCX、TXT、MD、CSV、TSV、XLSX、XLSM のいずれかを指定してください。",
        )
    if path.stat().st_size == 0:
        raise OntologySourceError("ONTOLOGY_SOURCE_EMPTY", "空の資料は登録できません。")
    with path.open("rb") as stream:
        prefix = stream.read(4096)
    if suffix == ".pdf" and not prefix.startswith(b"%PDF-"):
        raise OntologySourceError("ONTOLOGY_SOURCE_MIME_MISMATCH", "PDF の内容を確認できません。")
    if suffix in {".docx", ".xlsx", ".xlsm"}:
        try:
            with zipfile.ZipFile(path) as archive:
                names = frozenset(archive.namelist())
        except (OSError, zipfile.BadZipFile) as exc:
            raise OntologySourceError(
                "ONTOLOGY_SOURCE_ARCHIVE_INVALID", "Office ファイルが壊れています。"
            ) from exc
        expected = "word/document.xml" if suffix == ".docx" else "xl/workbook.xml"
        if expected not in names:
            raise OntologySourceError(
                "ONTOLOGY_SOURCE_MIME_MISMATCH", "拡張子と Office ファイル内容が一致しません。"
            )
    if suffix in {".txt", ".md", ".csv", ".tsv"} and b"\x00" in prefix:
        raise OntologySourceError(
            "ONTOLOGY_SOURCE_BINARY_TEXT", "テキスト資料にバイナリデータが含まれています。"
        )


def validate_source_media_type(filename: str, media_type: str) -> None:
    normalized = media_type.partition(";")[0].strip().lower()
    if not normalized or normalized == "application/octet-stream":
        return
    suffix = Path(filename).suffix.lower()
    if normalized not in _ALLOWED_MEDIA_TYPES.get(suffix, frozenset()):
        raise OntologySourceError(
            "ONTOLOGY_SOURCE_MIME_MISMATCH",
            "資料の Content-Type と拡張子が一致しません。",
        )


class OntologySourceStorage:
    """local / OCI Object Storage を同じ URI 契約で扱う。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def save_upload(
        self,
        *,
        profile_id: str,
        upload: UploadFile,
    ) -> OntologySourceDocument:
        filename = Path(upload.filename or "source").name
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_ONTOLOGY_SOURCE_SUFFIXES:
            raise OntologySourceError(
                "ONTOLOGY_SOURCE_FORMAT_UNSUPPORTED",
                "PDF、DOCX、TXT、MD、CSV、TSV、XLSX、XLSM のいずれかを指定してください。",
            )
        source_id = f"ontology_source_{uuid4().hex}"
        profile_storage_key = hashlib.sha256(profile_id.encode("utf-8")).hexdigest()[:24]
        target_dir = Path(self.settings.local_storage_dir).expanduser() / "ontology-sources"
        target_dir = target_dir / profile_storage_key / source_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        digest = hashlib.sha256()
        total = 0
        with target.open("wb") as stream:
            while chunk := await upload.read(_CHUNK_SIZE):
                total += len(chunk)
                if total > self.settings.max_upload_bytes:
                    stream.close()
                    target.unlink(missing_ok=True)
                    raise OntologySourceError(
                        "ONTOLOGY_SOURCE_TOO_LARGE",
                        "資料がアップロード上限を超えています。",
                    )
                digest.update(chunk)
                stream.write(chunk)
        media_type = (
            upload.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        )
        try:
            validate_source_media_type(filename, media_type)
            # 先頭 4 KiB と ZIP central directory の bounded local 検査。upload stream 自体も
            # local file へ逐次書込みしているため、ここだけ executor を作る必要はない。
            validate_source_path(filename, target)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        storage_uri = str(target)
        if self.settings.upload_storage_backend.strip().lower() == "oci":
            storage_uri = await asyncio.to_thread(
                self._put_object,
                profile_storage_key,
                source_id,
                filename,
                target,
            )
        return OntologySourceDocument(
            id=source_id,
            profile_id=profile_id,
            filename=filename,
            media_type=media_type,
            size_bytes=total,
            sha256=digest.hexdigest(),
            storage_uri=storage_uri,
        )

    def load(self, document: OntologySourceDocument) -> bytes:
        if document.storage_uri.startswith("oci://"):
            _, _, remainder = document.storage_uri.partition("oci://")
            bucket, _, object_name = remainder.partition("/")
            response = self._object_storage_client().get_object(
                self.settings.object_storage_namespace,
                bucket,
                object_name,
            )
            return bytes(response.data.content)
        return Path(document.storage_uri).read_bytes()

    def _put_object(
        self, profile_storage_key: str, source_id: str, filename: str, target: Path
    ) -> str:
        object_name = f"ontology-sources/{profile_storage_key}/{source_id}/{filename}"
        with target.open("rb") as stream:
            self._object_storage_client().put_object(
                self.settings.object_storage_namespace,
                self.settings.object_storage_bucket,
                object_name,
                stream,
            )
        return f"oci://{self.settings.object_storage_bucket}/{object_name}"

    def _object_storage_client(self) -> Any:
        oci_config = importlib.import_module("oci.config")
        object_storage = importlib.import_module("oci.object_storage")
        config = load_oci_config_without_prompt(
            oci_config,
            self.settings.oci_config_file,
            self.settings.resolved_oci_config_profile,
            region=self.settings.object_storage_region or self.settings.oci_region or None,
        )
        return object_storage.ObjectStorageClient(config)


def extract_ontology_source(
    source: OntologySourceDocument,
    content: bytes,
    *,
    vlm_page_runner: Callable[[bytes, int], str] | None = None,
) -> ExtractedOntologySource:
    validate_source_signature(source.filename, content)
    suffix = Path(source.filename).suffix.lower()
    if suffix in {".txt", ".md"}:
        return _extract_text(content)
    if suffix in {".csv", ".tsv"}:
        return _extract_delimited(content, delimiter="\t" if suffix == ".tsv" else ",")
    if suffix in {".xlsx", ".xlsm"}:
        return _extract_workbook(content)
    if suffix == ".docx":
        return _extract_docx(content)
    if suffix == ".pdf":
        return _extract_pdf(content, vlm_page_runner=vlm_page_runner)
    raise OntologySourceError("ONTOLOGY_SOURCE_FORMAT_UNSUPPORTED", "未対応の資料形式です。")


def _extract_text(content: bytes) -> ExtractedOntologySource:
    text = normalize_source_text(decode_source_text(content))
    chunks = [
        ExtractedSourceChunk(line, OntologyEvidenceLocatorKind.LINE, f"line:{index}")
        for index, line in enumerate(text.splitlines(), start=1)
        if line
    ]
    return ExtractedOntologySource(chunks=chunks, qa_pairs=[], warnings_ja=[])


def _qa_pairs_from_rows(rows: list[list[str]]) -> list[QaPair]:
    if not rows:
        return []
    headers = [value.strip().upper().replace("_", "") for value in rows[0]]
    question_keys = {"QUESTION", "質問", "TEXT", "PROMPT"}
    sql_keys = {"SQL", "ANSWERSQL", "回答SQL", "正解SQL"}
    question_index = next((i for i, value in enumerate(headers) if value in question_keys), None)
    sql_index = next((i for i, value in enumerate(headers) if value in sql_keys), None)
    if question_index is None or sql_index is None:
        return []
    result: list[QaPair] = []
    for row in rows[1:201]:
        question = row[question_index].strip() if len(row) > question_index else ""
        sql = row[sql_index].strip() if len(row) > sql_index else ""
        if question and sql and sql.split(None, 1)[0].upper() in {"SELECT", "WITH"}:
            result.append(QaPair(question=question, sql=sql))
    return result


def _extract_delimited(content: bytes, *, delimiter: str) -> ExtractedOntologySource:
    text = decode_source_text(content)
    rows = [
        [normalize_source_text(value) for value in row]
        for row in csv.reader(io.StringIO(text), delimiter=delimiter)
    ]
    chunks = [
        ExtractedSourceChunk(
            json.dumps(row, ensure_ascii=False),
            OntologyEvidenceLocatorKind.QA_ROW if index > 1 else OntologyEvidenceLocatorKind.LINE,
            f"row:{index}",
        )
        for index, row in enumerate(rows, start=1)
        if any(row)
    ]
    return ExtractedOntologySource(
        chunks=chunks, qa_pairs=_qa_pairs_from_rows(rows), warnings_ja=[]
    )


def _extract_workbook(content: bytes) -> ExtractedOntologySource:
    import openpyxl  # type: ignore[import-untyped]

    try:
        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:
        raise OntologySourceError(
            "ONTOLOGY_SOURCE_XLSX_INVALID", "Excel 資料を読み取れません。"
        ) from exc
    chunks: list[ExtractedSourceChunk] = []
    qa_pairs: list[QaPair] = []
    for sheet in workbook.worksheets:
        rows = [
            [normalize_source_text(str(value)) if value is not None else "" for value in row]
            for row in sheet.iter_rows(values_only=True)
        ]
        if not qa_pairs:
            qa_pairs = _qa_pairs_from_rows(rows)
        chunks.extend(
            ExtractedSourceChunk(
                json.dumps(row, ensure_ascii=False),
                OntologyEvidenceLocatorKind.SHEET_ROW,
                f"sheet:{sheet.title};row:{index}",
            )
            for index, row in enumerate(rows, start=1)
            if any(row)
        )
    return ExtractedOntologySource(chunks=chunks, qa_pairs=qa_pairs, warnings_ja=[])


def _extract_docx(content: bytes) -> ExtractedOntologySource:
    from docx import Document

    try:
        document = Document(io.BytesIO(content))
    except Exception as exc:
        raise OntologySourceError(
            "ONTOLOGY_SOURCE_DOCX_INVALID", "Word 資料を読み取れません。"
        ) from exc
    chunks = [
        ExtractedSourceChunk(
            normalize_source_text(paragraph.text),
            OntologyEvidenceLocatorKind.PARAGRAPH,
            f"paragraph:{index}",
        )
        for index, paragraph in enumerate(document.paragraphs, start=1)
        if normalize_source_text(paragraph.text)
    ]
    for table_index, table in enumerate(document.tables, start=1):
        for row_index, row in enumerate(table.rows, start=1):
            values = [normalize_source_text(cell.text) for cell in row.cells]
            if any(values):
                chunks.append(
                    ExtractedSourceChunk(
                        json.dumps(values, ensure_ascii=False),
                        OntologyEvidenceLocatorKind.SHEET_ROW,
                        f"table:{table_index};row:{row_index}",
                    )
                )
    return ExtractedOntologySource(chunks=chunks, qa_pairs=[], warnings_ja=[])


def _extract_pdf(
    content: bytes,
    *,
    vlm_page_runner: Callable[[bytes, int], str] | None,
) -> ExtractedOntologySource:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise OntologySourceError(
                "ONTOLOGY_SOURCE_PDF_ENCRYPTED", "暗号化 PDF は使用できません。"
            )
    except OntologySourceError:
        raise
    except Exception as exc:
        raise OntologySourceError(
            "ONTOLOGY_SOURCE_PDF_INVALID", "PDF 資料を読み取れません。"
        ) from exc
    chunks: list[ExtractedSourceChunk] = []
    warnings: list[str] = []
    pdfium_document: Any | None = None
    for index, page in enumerate(reader.pages, start=1):
        text = normalize_source_text(page.extract_text() or "")
        if not text and vlm_page_runner is not None:
            try:
                if pdfium_document is None:
                    import pypdfium2  # type: ignore[import-untyped]

                    pdfium_document = pypdfium2.PdfDocument(content)
                bitmap = pdfium_document[index - 1].render(scale=2)
                image = bitmap.to_pil()
                image_buffer = io.BytesIO()
                image.save(image_buffer, format="JPEG", quality=90)
                text = normalize_source_text(vlm_page_runner(image_buffer.getvalue(), index))
            except Exception as exc:
                warnings.append(f"PDF {index} ページ目の VLM OCR に失敗しました: {exc}")
        if not text:
            warnings.append(f"PDF {index} ページ目はテキストを抽出できませんでした。")
            continue
        chunks.append(ExtractedSourceChunk(text, OntologyEvidenceLocatorKind.PAGE, f"page:{index}"))
    return ExtractedOntologySource(chunks=chunks, qa_pairs=[], warnings_ja=warnings)
