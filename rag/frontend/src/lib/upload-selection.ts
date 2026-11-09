/** 文書アップロードで送る前に選んでおくファイルの扱い（#701）。 */

/**
 * ファイル選択の `accept` と、送る前の形式の確認に使う拡張子・MIME type。主要な形式だけを受ける（#4730）。
 * backend の `ACCEPTED_UPLOAD_EXTENSIONS` と `allowed_upload_content_types` と同じ（backend のテストで照合する）。
 */
export const ACCEPTED_UPLOAD_TYPES = [
  ".pdf",
  ".doc",
  ".docx",
  ".ppt",
  ".pptx",
  ".xls",
  ".xlsx",
  ".csv",
  ".txt",
  ".md",
  ".markdown",
  ".json",
  ".html",
  ".htm",
  ".jpg",
  ".jpeg",
  ".png",
  "application/pdf",
  "application/msword",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.ms-powerpoint",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  "application/vnd.ms-excel",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "text/csv",
  "application/csv",
  "text/plain",
  "text/markdown",
  "application/json",
  "text/html",
  "image/jpeg",
  "image/jpg",
  "image/png",
].join(",");

const ACCEPTED_EXTENSIONS = new Set(
  ACCEPTED_UPLOAD_TYPES.split(",").filter((item) => item.startsWith("."))
);

/** 案内に出す拡張子（ドット無し・大文字）。 */
export const ACCEPTED_UPLOAD_EXTENSION_LABELS = [...ACCEPTED_EXTENSIONS].map((ext) =>
  ext.slice(1).toUpperCase()
);

/**
 * 受け付けない形式のうち、変換の方法を案内する形式（#3351）。解析に要る依存（HEIC / HEIF は pi-heif の
 * libheif・EPUB は pandoc）をライセンスの理由で入れないため、送らずに止める。音声・動画は取り込まない
 * 形式で、同じく送らずに止める（#3369）。backend の `app.rag.source_profile.REJECTED_UPLOAD_EXTENSIONS`
 * と同じ拡張子（backend のテストで照合する）。
 */
export const REJECTED_UPLOAD_FORMATS = {
  ".heic": "heicImage",
  ".heif": "heicImage",
  ".epub": "epubBook",
  ".aac": "audioVideo",
  ".aif": "audioVideo",
  ".aiff": "audioVideo",
  ".amr": "audioVideo",
  ".flac": "audioVideo",
  ".m4a": "audioVideo",
  ".mp3": "audioVideo",
  ".oga": "audioVideo",
  ".ogg": "audioVideo",
  ".opus": "audioVideo",
  ".wav": "audioVideo",
  ".weba": "audioVideo",
  ".wma": "audioVideo",
  ".3gp": "audioVideo",
  ".avi": "audioVideo",
  ".m4v": "audioVideo",
  ".mkv": "audioVideo",
  ".mov": "audioVideo",
  ".mp4": "audioVideo",
  ".mpeg": "audioVideo",
  ".mpg": "audioVideo",
  ".ogv": "audioVideo",
  ".webm": "audioVideo",
  ".wmv": "audioVideo",
} as const;

export type RejectedUploadFormat = (typeof REJECTED_UPLOAD_FORMATS)[keyof typeof REJECTED_UPLOAD_FORMATS];

export type UploadFileProblem = "unsupported" | "tooLarge" | "empty" | RejectedUploadFormat;

function isRejectedUploadExtension(extension: string): extension is keyof typeof REJECTED_UPLOAD_FORMATS {
  return Object.hasOwn(REJECTED_UPLOAD_FORMATS, extension);
}

/** 送れないファイルの理由。送れるなら null。ドロップしたファイルは `accept` を通らないので、ここで確かめる。 */
export function uploadFileProblem(
  file: Pick<File, "name" | "size">,
  maxUploadBytes: number
): UploadFileProblem | null {
  const dot = file.name.lastIndexOf(".");
  const extension = dot >= 0 ? file.name.slice(dot).toLowerCase() : "";
  if (isRejectedUploadExtension(extension)) return REJECTED_UPLOAD_FORMATS[extension];
  if (!ACCEPTED_EXTENSIONS.has(extension)) return "unsupported";
  if (file.size === 0) return "empty";
  if (file.size > maxUploadBytes) return "tooLarge";
  return null;
}

function fileKey(file: Pick<File, "name" | "size" | "lastModified">): string {
  return `${file.name}\u0000${file.size}\u0000${file.lastModified}`;
}

/** 選んだファイルに足す。同じファイル（名前・サイズ・更新日時が同じ）は重ねない。 */
export function mergeUploadSelection(current: readonly File[], added: readonly File[]): File[] {
  const seen = new Set(current.map(fileKey));
  const merged = [...current];
  for (const file of added) {
    const key = fileKey(file);
    if (seen.has(key)) continue;
    seen.add(key);
    merged.push(file);
  }
  return merged;
}
