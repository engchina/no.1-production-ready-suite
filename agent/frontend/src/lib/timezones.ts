/** 利用状況と自動実行で共通のブラウザのタイムゾーン。 */
const DEFAULT_TIMEZONE = "Asia/Tokyo";
const OFFSET_TIMEZONE = /^[+-](?:[01]\d|2[0-3]):[0-5]\d$/;

/** 空欄は backend の既定を使う。地域名は夏時間を含むため時差に変換しない。 */
export function isValidTimezone(value: string): boolean {
  const timezone = value.trim();
  if (!timezone || OFFSET_TIMEZONE.test(timezone)) return true;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: timezone });
    return true;
  } catch {
    return false;
  }
}

/** 地域名か固定時差を返し、取得できない値は backend と同じ既定へ戻す。 */
export function browserTimezone(): string {
  try {
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone?.trim();
    return timezone && isValidTimezone(timezone) ? timezone : DEFAULT_TIMEZONE;
  } catch {
    return DEFAULT_TIMEZONE;
  }
}
