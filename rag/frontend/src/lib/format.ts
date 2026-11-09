/** 表示用フォーマッタ（ロケール ja-JP。日時はシステムの設定の時間帯。#3265）。 */

import { displayDateTimeFormat, parseApiDateTime } from "@production-ready/ui";

export { parseApiDateTime };

const numberFormat = new Intl.NumberFormat("ja-JP");

/** 整数・件数のカンマ区切り。 */
export function formatNumber(value: number): string {
  return numberFormat.format(value);
}

const dateTimeFormat = displayDateTimeFormat({
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

/** ISO 文字列を「MM/DD HH:mm」へ。未設定・無効値はダッシュ。 */
export function formatDateTime(iso: string | null | undefined): string {
  const date = parseApiDateTime(iso);
  return date ? dateTimeFormat.format(date) : "—";
}

/** バイト数を人間可読サイズへ。 */
export function formatBytes(bytes: number | null): string {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(1)} ${units[unit]}`;
}
