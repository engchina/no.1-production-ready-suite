import { useCallback, useEffect, useState } from "react";

import { apiGet } from "@/lib/api";
import {
  clearActiveJobSnapshot,
  isJobInFlight,
  isJobTerminal,
  persistActiveJobSnapshot,
  readActiveJobSnapshot,
  type ActiveJobStorage,
} from "./jobPersistence";
import type { JobCreateData, JobData, Nl2SqlResult } from "./types";

interface UseNl2SqlJobPollingOptions {
  onResult(result: Nl2SqlResult): void;
  onError(message: string): void;
  onHistoryRefresh(): Promise<void> | void;
  pollIntervalMs?: number;
}

function getBrowserStorage(): ActiveJobStorage | null {
  return typeof window === "undefined" ? null : window.localStorage;
}

export function useNl2SqlJobPolling({
  onResult,
  onError,
  onHistoryRefresh,
  pollIntervalMs = 2500,
}: UseNl2SqlJobPollingOptions) {
  const [job, setJob] = useState<JobData | null>(null);
  const [jobStartedAt, setJobStartedAt] = useState<number | null>(null);

  const pollJob = useCallback(
    async (jobId: string) => {
      const data = await apiGet<JobData>(`/api/nl2sql/jobs/${jobId}`);
      setJob(data);
      if (isJobTerminal(data.status)) {
        const storage = getBrowserStorage();
        if (storage) clearActiveJobSnapshot(storage);
        if (data.result) onResult(data.result);
        if (data.error_message) onError(data.error_message);
        await onHistoryRefresh();
      }
      return data;
    },
    [onError, onHistoryRefresh, onResult]
  );

  const trackJob = useCallback((data: JobCreateData, startedAtMs: number) => {
    const storage = getBrowserStorage();
    if (storage) persistActiveJobSnapshot(storage, data.job_id, startedAtMs);
    setJobStartedAt(startedAtMs);
    setJob({ ...data, result: null, error_message: null, timing: null });
  }, []);

  const clearTrackedJob = useCallback(() => {
    const storage = getBrowserStorage();
    if (storage) clearActiveJobSnapshot(storage);
    setJob(null);
    setJobStartedAt(null);
  }, []);

  useEffect(() => {
    const storage = getBrowserStorage();
    if (!storage) return;
    const snapshot = readActiveJobSnapshot(storage, Date.now());
    if (!snapshot) return;
    setJobStartedAt(snapshot.startedAtMs);
    void pollJob(snapshot.jobId);
  }, [pollJob]);

  useEffect(() => {
    if (!job || !isJobInFlight(job.status)) return undefined;
    const timer = window.setInterval(() => void pollJob(job.job_id), pollIntervalMs);
    return () => window.clearInterval(timer);
  }, [job, pollIntervalMs, pollJob]);

  return {
    job,
    jobStartedAt,
    pollJob,
    trackJob,
    clearTrackedJob,
  };
}
