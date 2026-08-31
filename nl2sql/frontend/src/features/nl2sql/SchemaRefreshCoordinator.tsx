import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";

import { toast } from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";

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
  const reportedTerminal = useRef("");
  const jobQuery = useSchemaRefreshJob(trackedJobId);

  const track = useCallback(
    (jobOrId: SchemaRefreshJob | string) => {
      const job = typeof jobOrId === "string" ? null : jobOrId;
      const jobId = typeof jobOrId === "string" ? jobOrId : jobOrId.job_id;
      reportedTerminal.current = "";
      setCompletedJob(null);
      setError("");
      setTrackedJobId(jobId);
      setTrackedSnapshot(job);
      if (job && jobId) {
        queryClient.setQueryData(nl2sqlIncrementalKeys.schemaRefreshJob(jobId), job);
      }
      if (job && schemaRefreshJobIsActive(job)) {
        queryClient.setQueryData<SchemaRefreshActiveJobData>(
          nl2sqlIncrementalKeys.activeSchemaRefreshJob,
          { active_job: job },
        );
      }
    },
    [queryClient],
  );

  useEffect(() => {
    const activeJob = activeJobQuery.data?.active_job;
    if (!activeJob || activeJob.job_id === trackedJobId) return;
    track(activeJob);
  }, [activeJobQuery.data?.active_job, track, trackedJobId]);

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

  useEffect(() => {
    if (!job || (job.status !== "done" && job.status !== "error")) return;
    const reportKey = `${job.job_id}:${job.status}`;
    if (reportedTerminal.current === reportKey) return;
    reportedTerminal.current = reportKey;
    setCompletedJob(job);
    queryClient.setQueryData<SchemaRefreshActiveJobData>(
      nl2sqlIncrementalKeys.activeSchemaRefreshJob,
      { active_job: null },
    );
    if (job.status === "done") {
      setError("");
      void queryClient.invalidateQueries({ queryKey: ["schema"] });
      void queryClient.invalidateQueries({ queryKey: ["nl2sql", "db-admin"] });
      toast.success(t("common.action.schemaRefreshed"));
      return;
    }
    // 継続する失敗は固定面(各ページの Banner / FormStatus + header status badge)を
    // 正本とし、Toast は出さない(messaging spec §0.6 / §4.2)。
    setError(schemaRefreshJobErrorMessage(job));
  }, [job, queryClient]);

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
