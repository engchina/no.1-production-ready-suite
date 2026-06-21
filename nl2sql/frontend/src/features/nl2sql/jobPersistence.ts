import type { JobStatus } from "./types";

export const ACTIVE_JOB_ID_KEY = "nl2sql.activeJobId";
export const ACTIVE_JOB_STARTED_AT_KEY = "nl2sql.activeJobStartedAt";

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
  fallbackStartedAtMs: number
): ActiveJobSnapshot | null {
  const jobId = storage.getItem(ACTIVE_JOB_ID_KEY);
  if (!jobId) return null;
  const storedStartedAt = Number(storage.getItem(ACTIVE_JOB_STARTED_AT_KEY));
  return {
    jobId,
    startedAtMs: Number.isFinite(storedStartedAt) && storedStartedAt > 0
      ? storedStartedAt
      : fallbackStartedAtMs,
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
