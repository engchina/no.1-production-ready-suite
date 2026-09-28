import type { BatchUploadFailedItem, BatchUploadResult } from "./api";

/**
 * 文書アップロードの送信計画（#280）。
 *
 * backend の上限は「1 ファイル」あたり（`RAG_MAX_UPLOAD_BYTES`、既定 200 MiB）だが、前段の nginx の
 * `client_max_body_size`（210M）は「1 リクエスト」あたりに効く。複数ファイルを 1 リクエストで送ると、
 * 1 件ずつは上限内でも合計が 210M を超えた時点で一括アップロード全体が 413 になる。
 * そこで、1 リクエストの合計を 1 ファイルの上限以内に収めるよう分けて送り、上限を超えるファイルは
 * 送る前に失敗として扱う（大きなファイルを最後まで送ってから拒否されるのを避ける）。
 */

/** upload-storage 設定を取得できないときの上限（backend の既定値と同じ）。 */
export const DEFAULT_MAX_UPLOAD_BYTES = 200 * 1024 * 1024;

/** 1 リクエストに含めるファイル数の上限（multipart の解析上限 1000 より十分小さくする）。 */
export const MAX_FILES_PER_UPLOAD_REQUEST = 50;

type SizedFile = Pick<File, "size">;

/** 上限の表示（200 MiB → "200 MB"。整数にならないときだけ小数 1 桁）。 */
export function formatUploadLimit(bytes: number): string {
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const text = Number.isInteger(value) ? String(value) : value.toFixed(1);
  return `${text} ${units[unit]}`;
}

/**
 * 選択したファイルを、1 リクエストの合計が `maxBytes` 以内になるまとまり（選択した順を保つ）へ分ける。
 * 1 ファイルで `maxBytes` を超えるものは `oversized` に分け、送らない。
 */
export function planUploadRequests<T extends SizedFile>(
  files: readonly T[],
  maxBytes: number,
  maxFilesPerRequest: number = MAX_FILES_PER_UPLOAD_REQUEST,
): { groups: T[][]; oversized: T[] } {
  const limit = Number.isFinite(maxBytes) && maxBytes > 0 ? maxBytes : DEFAULT_MAX_UPLOAD_BYTES;
  const groups: T[][] = [];
  const oversized: T[] = [];
  let current: T[] = [];
  let currentBytes = 0;
  for (const file of files) {
    if (file.size > limit) {
      oversized.push(file);
      continue;
    }
    if (
      current.length > 0 &&
      (currentBytes + file.size > limit || current.length >= maxFilesPerRequest)
    ) {
      groups.push(current);
      current = [];
      currentBytes = 0;
    }
    current.push(file);
    currentBytes += file.size;
  }
  if (current.length > 0) groups.push(current);
  return { groups, oversized };
}

/** 送らずに失敗とするファイルの結果（backend の 413 と同じ形）。 */
export function failedUploadItem(
  file: Pick<File, "name">,
  statusCode: number,
  message: string,
): BatchUploadFailedItem {
  return { file_name: file.name, status_code: statusCode, message, source_profile: null };
}

/** 分けて送った結果と、送らずに失敗としたファイルを 1 つの一括アップロード結果にまとめる。 */
export function mergeBatchUploadResults(
  results: readonly BatchUploadResult[],
  extraFailedItems: readonly BatchUploadFailedItem[] = [],
): BatchUploadResult {
  const items = results.flatMap((result) => result.items);
  const failedItems = [...results.flatMap((result) => result.failed_items), ...extraFailedItems];
  return {
    items,
    failed_items: failedItems,
    total_count: items.length + failedItems.length,
    uploaded_count: items.length,
    failed_count: failedItems.length,
    queued_count: results.reduce((sum, result) => sum + result.queued_count, 0),
    skipped_count: results.reduce((sum, result) => sum + result.skipped_count, 0),
  };
}
