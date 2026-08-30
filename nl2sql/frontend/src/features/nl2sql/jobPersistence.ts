// node:test(jiti)から直接 import されるため、"@/" alias でなく相対 path を使う。
import { API_TIMEOUT_MS } from "../../lib/requestPolicy";

import type { JobStatus } from "./types";

export const ACTIVE_JOB_ID_KEY = "nl2sql.activeJobId";
export const ACTIVE_JOB_STARTED_AT_KEY = "nl2sql.activeJobStartedAt";

// longRunningJob の HTTP timeout と同じ寿命。これを超えた snapshot は復元せず破棄する
// (失効 job への無限ポーリング再開を防ぐ)。
export const ACTIVE_JOB_SNAPSHOT_TTL_MS = API_TIMEOUT_MS.longRunningJob;

export interface ActiveJobStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export interface ActiveJobSnapshot {
  jobId: string;
  startedAtMs: number;
}

export function isJobInFlight(status: JobStatus | null | undefined): boolean {
  return status === "pending" || status === "running";
}

export function isJobTerminal(status: JobStatus | null | undefined): boolean {
  return status === "done" || status === "error";
}

export function readActiveJobSnapshot(
  storage: ActiveJobStorage,
  nowMs: number
): ActiveJobSnapshot | null {
  const jobId = storage.getItem(ACTIVE_JOB_ID_KEY);
  if (!jobId) return null;
  const storedStartedAt = Number(storage.getItem(ACTIVE_JOB_STARTED_AT_KEY));
  const startedAtValid = Number.isFinite(storedStartedAt) && storedStartedAt > 0;
  if (startedAtValid && nowMs - storedStartedAt > ACTIVE_JOB_SNAPSHOT_TTL_MS) {
    clearActiveJobSnapshot(storage);
    return null;
  }
  if (!startedAtValid) {
    // 次回 mount から TTL が効くように現在時刻で補完して書き戻す。
    storage.setItem(ACTIVE_JOB_STARTED_AT_KEY, String(nowMs));
  }
  return {
    jobId,
    startedAtMs: startedAtValid ? storedStartedAt : nowMs,
  };
}

export function persistActiveJobSnapshot(
  storage: ActiveJobStorage,
  jobId: string,
  startedAtMs: number
) {
  storage.setItem(ACTIVE_JOB_ID_KEY, jobId);
  storage.setItem(ACTIVE_JOB_STARTED_AT_KEY, String(startedAtMs));
}

export function clearActiveJobSnapshot(storage: ActiveJobStorage) {
  storage.removeItem(ACTIVE_JOB_ID_KEY);
  storage.removeItem(ACTIVE_JOB_STARTED_AT_KEY);
}
