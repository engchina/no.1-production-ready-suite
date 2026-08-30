import { useCallback, useEffect, useRef, useState } from "react";

import { apiGet, isAbortError } from "@/lib/api";
import { t } from "@/lib/i18n";
import {
  clearActiveJobSnapshot,
  isJobInFlight,
  isJobTerminal,
  persistActiveJobSnapshot,
  readActiveJobSnapshot,
  type ActiveJobStorage,
} from "./jobPersistence";
import { classifyPollFailure } from "./jobPollingPolicy";
import type { JobCreateData, JobData, Nl2SqlResult } from "./types";

interface UseNl2SqlJobPollingOptions {
  onResult(result: Nl2SqlResult): void;
  /** job 自体の失敗(終端遷移の error_message)。表示は OperationStatusStrip 側が正本。 */
  onJobFailed(message: string): void;
  /** ポーリング通信の断念(連続失敗)/ job 消失(404)。追跡は解除済みで UI ロックは解ける。 */
  onPollingLost(message: string): void;
  onHistoryRefresh(): Promise<void> | void;
  /** 履歴更新の失敗。結果自体は成功しているため、失敗表示に変えてはならない。 */
  onHistoryRefreshFailed?(cause: unknown): void;
  pollIntervalMs?: number;
}

function getBrowserStorage(): ActiveJobStorage | null {
  return typeof window === "undefined" ? null : window.localStorage;
}

/** 復元直後に表示する in-flight プレースホルダ。実データは次のポーリングで上書きされる。 */
function syntheticInFlightJob(jobId: string, startedAtMs: number): JobData {
  return {
    job_id: jobId,
    status: "running",
    created_at: new Date(startedAtMs).toISOString(),
    result: null,
    error_message: null,
    warning_message: null,
    timing: null,
    steps: [],
  };
}

export function useNl2SqlJobPolling({
  onResult,
  onJobFailed,
  onPollingLost,
  onHistoryRefresh,
  onHistoryRefreshFailed,
  pollIntervalMs = 2500,
}: UseNl2SqlJobPollingOptions) {
  const [job, setJob] = useState<JobData | null>(null);
  const [jobStartedAt, setJobStartedAt] = useState<number | null>(null);
  const consecutiveFailuresRef = useRef(0);

  const stopTracking = useCallback(() => {
    const storage = getBrowserStorage();
    if (storage) clearActiveJobSnapshot(storage);
    consecutiveFailuresRef.current = 0;
    setJob(null);
    setJobStartedAt(null);
  }, []);

  const pollJob = useCallback(
    async (jobId: string, signal?: AbortSignal) => {
      const data = await apiGet<JobData>(`/api/nl2sql/jobs/${jobId}`, { signal });
      if (signal?.aborted) return data;
      consecutiveFailuresRef.current = 0;
      setJob(data);
      if (isJobTerminal(data.status)) {
        const storage = getBrowserStorage();
        if (storage) clearActiveJobSnapshot(storage);
        if (data.result) onResult(data.result);
        if (data.error_message) onJobFailed(data.error_message);
        if (signal?.aborted) return data;
        try {
          await onHistoryRefresh();
        } catch (cause) {
          if (!isAbortError(cause)) onHistoryRefreshFailed?.(cause);
        }
      }
      return data;
    },
    [onHistoryRefresh, onHistoryRefreshFailed, onJobFailed, onResult]
  );

  // ポーリング失敗を分類し、断念時は追跡を解除して UI ロックを解く。
  // 旧実装は失敗を onError へ流すだけで追跡を解除せず、通信断で画面が永久ロックしていた。
  const handlePollFailure = useCallback(
    (cause: unknown) => {
      if (isAbortError(cause)) return;
      const action = classifyPollFailure(cause, ++consecutiveFailuresRef.current);
      if (action === "retry") return;
      stopTracking();
      onPollingLost(action === "job-gone" ? t("nl2sql.job.expired") : t("nl2sql.job.pollingLost"));
    },
    [onPollingLost, stopTracking]
  );

  const trackJob = useCallback((data: JobCreateData, startedAtMs: number) => {
    const storage = getBrowserStorage();
    if (storage) persistActiveJobSnapshot(storage, data.job_id, startedAtMs);
    consecutiveFailuresRef.current = 0;
    setJobStartedAt(startedAtMs);
    setJob({ ...data, result: null, error_message: null, warning_message: null, timing: null });
  }, []);

  const clearTrackedJob = useCallback(() => {
    stopTracking();
  }, [stopTracking]);

  // 再訪時の復元は合成 in-flight job を置くだけにし、実際の取得・失敗処理は
  // 下の interval effect に一本化する(失効 job への失敗リクエスト連発を防ぐ)。
  useEffect(() => {
    const storage = getBrowserStorage();
    if (!storage) return;
    const snapshot = readActiveJobSnapshot(storage, Date.now());
    if (!snapshot) return;
    setJobStartedAt(snapshot.startedAtMs);
    setJob(syntheticInFlightJob(snapshot.jobId, snapshot.startedAtMs));
  }, []);

  // job オブジェクトではなく「in-flight な job_id」へ依存させる。
  // 各ポーリング応答(setJob)で effect を張り直さず、即時 tick + 固定間隔で追跡できる。
  const activeJobId = job && isJobInFlight(job.status) ? job.job_id : null;
  useEffect(() => {
    if (!activeJobId) return undefined;
    let controller: AbortController | null = null;
    const tick = () => {
      controller?.abort();
      controller = new AbortController();
      void pollJob(activeJobId, controller.signal).catch(handlePollFailure);
    };
    tick();
    const timer = window.setInterval(tick, pollIntervalMs);
    return () => {
      controller?.abort();
      window.clearInterval(timer);
    };
  }, [activeJobId, handlePollFailure, pollIntervalMs, pollJob]);

  return {
    job,
    jobStartedAt,
    pollJob,
    trackJob,
    clearTrackedJob,
  };
}
