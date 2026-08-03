export type OperationTimestamp = number | string | null | undefined;

/** Date/ISO の開始・終了時刻を epoch milliseconds へ正規化する。 */
export function operationTimestampMs(value: OperationTimestamp): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

/** 開始時刻からの経過 milliseconds。端末時計が戻った場合も負数にしない。 */
export function elapsedMsSince(startedAtMs: number, nowMs = Date.now()): number {
  return Math.max(0, nowMs - startedAtMs);
}

/** 開始時刻からの経過 seconds。既存 NL2SQL timer との互換用。 */
export function elapsedSecondsSince(startedAtMs: number, nowMs = Date.now()): number {
  return Math.floor(elapsedMsSince(startedAtMs, nowMs) / 1000);
}

/**
 * UI 上で動く timer 用の固定桁フォーマット。
 * 1時間未満は mm:ss、1時間以上は h:mm:ss としてレイアウトシフトを抑える。
 */
export function formatElapsedClock(ms?: number | null): string {
  const totalSeconds = Math.max(0, Math.floor((ms ?? 0) / 1000));
  const seconds = totalSeconds % 60;
  const totalMinutes = Math.floor(totalSeconds / 60);
  const minutes = totalMinutes % 60;
  const hours = Math.floor(totalMinutes / 60);
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }
  return `${String(totalMinutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

/** 技術結果・履歴用。1秒未満の milliseconds 精度は従来どおり維持する。 */
export function formatElapsedDuration(ms?: number | null): string {
  if (ms === null || ms === undefined) return "-";
  const normalized = Math.max(0, ms);
  if (normalized < 1000) return `${Math.round(normalized)}ms`;
  if (normalized < 60_000) return `${(normalized / 1000).toFixed(1)}秒`;
  return formatElapsedClock(normalized);
}

/** server timestamp から確定済みまたは進行中の経過時間を求める。 */
export function elapsedMsBetween(
  startedAt: OperationTimestamp,
  finishedAt: OperationTimestamp,
  nowMs = Date.now(),
): number | null {
  const start = operationTimestampMs(startedAt);
  if (start === null) return null;
  const end = operationTimestampMs(finishedAt) ?? nowMs;
  return elapsedMsSince(start, end);
}
