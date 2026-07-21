import { useCallback, useEffect, useState } from "react";

import { apiGet, isAbortError } from "@/lib/api";
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
    async (jobId: string, signal?: AbortSignal) => {
      const data = await apiGet<JobData>(`/api/nl2sql/jobs/${jobId}`, { signal });
      if (signal?.aborted) return data;
      setJob(data);
      if (isJobTerminal(data.status)) {
        const storage = getBrowserStorage();
        if (storage) clearActiveJobSnapshot(storage);
        if (data.result) onResult(data.result);
        if (data.error_message) onError(data.error_message);
        if (signal?.aborted) return data;
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
    const controller = new AbortController();
    setJobStartedAt(snapshot.startedAtMs);
    void pollJob(snapshot.jobId, controller.signal).catch((cause: unknown) => {
      if (!isAbortError(cause)) {
        onError(cause instanceof Error ? cause.message : "Job status check failed");
      }
    });
    return () => controller.abort();
  }, [onError, pollJob]);

  useEffect(() => {
    if (!job || !isJobInFlight(job.status)) return undefined;
    let controller: AbortController | null = null;
    const tick = () => {
      controller?.abort();
      controller = new AbortController();
      void pollJob(job.job_id, controller.signal).catch((cause: unknown) => {
        if (!isAbortError(cause)) {
          onError(cause instanceof Error ? cause.message : "Job status check failed");
        }
      });
    };
    const timer = window.setInterval(tick, pollIntervalMs);
    return () => {
      controller?.abort();
      window.clearInterval(timer);
    };
  }, [job, onError, pollIntervalMs, pollJob]);

  return {
    job,
    jobStartedAt,
    pollJob,
    trackJob,
    clearTrackedJob,
  };
}
