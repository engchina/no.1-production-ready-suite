/** 文書アップロードで送る前に選んでおくファイルの扱い（#701）。 */

/** ファイル選択の `accept` と、送る前の形式の確認に使う拡張子・MIME type。 */
export const ACCEPTED_UPLOAD_TYPES = [
  ".pdf",
  ".gif",
  ".png",
  ".jpg",
  ".jpeg",
  ".webp",
  ".tif",
  ".tiff",
  ".txt",
  ".md",
  ".markdown",
  ".csv",
  ".tsv",
  ".json",
  ".jsonl",
  ".xml",
  ".ndjson",
  ".html",
  ".htm",
  ".xhtml",
  ".eml",
  ".msg",
  ".doc",
  ".docx",
  ".ppt",
  ".pptx",
  ".xls",
  ".xlsx",
  ".aac",
  ".flac",
  ".m4a",
  ".mp3",
  ".ogg",
  ".wav",
  "application/pdf",
  "image/gif",
  "image/jpeg",
  "image/jpg",
  "image/png",
  "image/webp",
  "image/tif",
  "image/tiff",
  "text/plain",
  "text/markdown",
  "text/csv",
  "text/tab-separated-values",
  "text/html",
  "application/xhtml+xml",
  "application/json",
  "application/jsonl",
  "application/jsonlines",
  "application/ndjson",
  "application/xml",
  "application/csv",
  "application/x-ndjson",
  "message/rfc822",
  "application/eml",
  "application/vnd.ms-outlook",
  "application/x-msg",
  "application/msword",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.ms-powerpoint",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  "application/vnd.ms-excel",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "audio/aac",
  "audio/flac",
  "audio/mp3",
  "audio/mpeg",
  "audio/mp4",
  "audio/ogg",
  "audio/wave",
  "audio/wav",
  "audio/x-flac",
  "audio/x-m4a",
  "audio/x-wav",
  "application/ogg",
].join(",");

const ACCEPTED_EXTENSIONS = new Set(
  ACCEPTED_UPLOAD_TYPES.split(",").filter((item) => item.startsWith("."))
);

/** 案内に出す拡張子（ドット無し・大文字）。 */
export const ACCEPTED_UPLOAD_EXTENSION_LABELS = [...ACCEPTED_EXTENSIONS].map((ext) =>
  ext.slice(1).toUpperCase()
);

export type UploadFileProblem = "unsupported" | "tooLarge" | "empty";

/** 送れないファイルの理由。送れるなら null。ドロップしたファイルは `accept` を通らないので、ここで確かめる。 */
export function uploadFileProblem(
  file: Pick<File, "name" | "size">,
  maxUploadBytes: number
): UploadFileProblem | null {
  const dot = file.name.lastIndexOf(".");
  const extension = dot >= 0 ? file.name.slice(dot).toLowerCase() : "";
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
