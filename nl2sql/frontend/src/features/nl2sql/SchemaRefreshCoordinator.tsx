import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";

import { toast } from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";
import { useValuesChanged } from "@/lib/render-sync";

import {
  nl2sqlIncrementalKeys,
  useActiveSchemaRefreshJob,
  useSchemaRefreshJob,
  useStartSchemaRefresh,
} from "./incrementalQueries";
import { schemaRefreshJobErrorMessage, schemaRefreshJobIsActive } from "./schemaRefreshPresentation";
import type { SchemaRefreshActiveJobData, SchemaRefreshJob } from "./types";

export interface SchemaRefreshCoordinatorValue {
  job: SchemaRefreshJob | null;
  isStarting: boolean;
  isRefreshing: boolean;
  completedJob: SchemaRefreshJob | null;
  error: string;
  start: () => Promise<SchemaRefreshJob>;
  track: (job: SchemaRefreshJob | string) => void;
  clearError: () => void;
}

const SchemaRefreshContext = createContext<SchemaRefreshCoordinatorValue | null>(null);

export function useSchemaRefreshCoordinator() {
  const value = useContext(SchemaRefreshContext);
  if (!value) {
    throw new Error("useSchemaRefreshCoordinator は SchemaRefreshCoordinator の配下で使用してください。");
  }
  return value;
}

export function SchemaRefreshCoordinator({
  children,
  discoveryEnabled = true,
}: {
  children: ReactNode;
  discoveryEnabled?: boolean;
}) {
  const queryClient = useQueryClient();
  const activeJobQuery = useActiveSchemaRefreshJob(discoveryEnabled);
  const startMutation = useStartSchemaRefresh();
  const [trackedJobId, setTrackedJobId] = useState("");
  const [trackedSnapshot, setTrackedSnapshot] = useState<SchemaRefreshJob | null>(null);
  const [completedJob, setCompletedJob] = useState<SchemaRefreshJob | null>(null);
  const [error, setError] = useState("");
  // 終端を報告済みの job（`<job_id>:<status>`）。track で空に戻す。
  const [reportedTerminal, setReportedTerminal] = useState("");
  // 実行中の job を見つけて追跡し始めた job。job の cache の seed は effect で行う。
  const [adoptedActiveJob, setAdoptedActiveJob] = useState<SchemaRefreshJob | null>(null);
  const jobQuery = useSchemaRefreshJob(trackedJobId);

  const track = useCallback(
    (jobOrId: SchemaRefreshJob | string) => {
      const job = typeof jobOrId === "string" ? null : jobOrId;
      const jobId = typeof jobOrId === "string" ? jobOrId : jobOrId.job_id;
      setReportedTerminal("");
      setCompletedJob(null);
      setError("");
      setTrackedJobId(jobId);
      setTrackedSnapshot(job);
      if (job) seedSchemaRefreshJobCache(queryClient, job);
    },
    [queryClient],
  );

  // 別の実行中の job が見つかったレンダーで、その job の追跡に切り替える（effect で setState しない）。
  const activeJob = activeJobQuery.data?.active_job;
  if (activeJob && activeJob.job_id !== trackedJobId) {
    setReportedTerminal("");
    setCompletedJob(null);
    setError("");
    setTrackedJobId(activeJob.job_id);
    setTrackedSnapshot(activeJob);
    setAdoptedActiveJob(activeJob);
  }
  useEffect(() => {
    if (adoptedActiveJob) seedSchemaRefreshJobCache(queryClient, adoptedActiveJob);
  }, [adoptedActiveJob, queryClient]);

  const job = jobQuery.data ?? trackedSnapshot;
  const isStarting = startMutation.isPending;
  const isRefreshing =
    isStarting ||
    schemaRefreshJobIsActive(job) ||
    (Boolean(trackedJobId) && !job);

  const start = useCallback(async () => {
    setError("");
    try {
      const nextJob = await startMutation.mutateAsync();
      track(nextJob);
      if (nextJob.status !== "done") {
        toast.info(t("dataMgmt.schemaJob.accepted"));
      }
      return nextJob;
    } catch (cause) {
      const message =
        cause instanceof Error ? cause.message : t("dataMgmt.schemaJob.submitError");
      // 表示は各ページの固定面(Banner / FormStatus)が正本。Toast と二重表示しない
      // (messaging spec §0.6)。context の error だけを更新する。
      setError(message);
      throw cause;
    }
  }, [startMutation, track]);

  // job が終端になったレンダーで、完了した job と error を直す（effect で setState しない）。
  const jobChanged = useValuesChanged([job]);
  const terminalKey =
    job && (job.status === "done" || job.status === "error") ? `${job.job_id}:${job.status}` : "";
  if (jobChanged && job && terminalKey && terminalKey !== reportedTerminal) {
    setReportedTerminal(terminalKey);
    setCompletedJob(job);
    if (job.status === "done") {
      setError("");
    } else {
      // 継続する失敗は固定面(各ページの Banner / FormStatus + header status badge)を
      // 正本とし、Toast は出さない(messaging spec §0.6 / §4.2)。
      setError(schemaRefreshJobErrorMessage(job));
    }
  }

  // 終端を報告したら（completedJob が変わったら）、cache の更新・再取得・Toast を行う。
  useEffect(() => {
    if (!completedJob) return;
    queryClient.setQueryData<SchemaRefreshActiveJobData>(
      nl2sqlIncrementalKeys.activeSchemaRefreshJob,
      { active_job: null },
    );
    if (completedJob.status !== "done") return;
    void queryClient.invalidateQueries({ queryKey: ["schema"] });
    void queryClient.invalidateQueries({ queryKey: ["nl2sql", "db-admin"] });
    toast.success(t("common.action.schemaRefreshed"));
  }, [completedJob, queryClient]);

  const value = useMemo<SchemaRefreshCoordinatorValue>(
    () => ({
      job,
      isStarting,
      isRefreshing,
      completedJob,
      error,
      start,
      track,
      clearError: () => setError(""),
    }),
    [completedJob, error, isRefreshing, isStarting, job, start, track],
  );

  return <SchemaRefreshContext.Provider value={value}>{children}</SchemaRefreshContext.Provider>;
}

/** 追跡を始めた job を job の cache と（実行中なら）実行中の job の cache に入れる。 */
function seedSchemaRefreshJobCache(
  queryClient: ReturnType<typeof useQueryClient>,
  job: SchemaRefreshJob,
) {
  if (job.job_id) {
    queryClient.setQueryData(nl2sqlIncrementalKeys.schemaRefreshJob(job.job_id), job);
  }
  if (schemaRefreshJobIsActive(job)) {
    queryClient.setQueryData<SchemaRefreshActiveJobData>(
      nl2sqlIncrementalKeys.activeSchemaRefreshJob,
      { active_job: job },
    );
  }
}
