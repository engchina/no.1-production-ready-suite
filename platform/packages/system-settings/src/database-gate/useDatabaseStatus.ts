import { useQuery, useQueryClient } from "@tanstack/react-query";

import { DATABASE_STATUS_QUERY_KEY, type DatabaseStatusApi, type DatabaseStatusData } from "./types";

/** 接続先が変わったとき（`context_id` が変わったとき）に呼ぶ。業務データの cache を捨てる等。 */
export type DatabaseContextChangeHandler = (
  previous: DatabaseStatusData,
  next: DatabaseStatusData,
) => Promise<void> | void;

export interface UseDatabaseStatusOptions {
  enabled?: boolean;
  onContextChange?: DatabaseContextChangeHandler;
}

/** 短時間は cache し、画面の移動ごとに DB を確かめ直さない。 */
export const DATABASE_STATUS_STALE_TIME_MS = 15_000;

/**
 * DB の状態（`GET /api/ready/database`）。3 製品で同じ query key・cache 時間にする（#325）。
 * 更新の実行中だけ定期確認する。通信の失敗は最大2回再試行し、cache は維持する。
 */
export function useDatabaseStatus(
  api: DatabaseStatusApi,
  { enabled = true, onContextChange }: UseDatabaseStatusOptions = {},
) {
  const qc = useQueryClient();
  return useQuery({
    queryKey: DATABASE_STATUS_QUERY_KEY,
    queryFn: async ({ signal }) => {
      const next = await api.getDatabaseStatus({ signal });
      const previous = qc.getQueryData<DatabaseStatusData>(DATABASE_STATUS_QUERY_KEY);
      if (
        onContextChange &&
        previous?.context_id &&
        next.context_id &&
        previous.context_id !== next.context_id
      ) {
        await onContextChange(previous, next);
      }
      return next;
    },
    enabled,
    staleTime: DATABASE_STATUS_STALE_TIME_MS,
    retryOnMount: false,
    retry: (failureCount, error) => {
      const status = (error as { status?: number }).status;
      // 認証・権限などの恒久的な 4xx は、通常のエラー処理に任せる。
      return failureCount < 2 && (status === undefined || status === 0 || status === 408 || status === 429 || status >= 500);
    },
    retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 5000),
    refetchInterval: (query) => query.state.status === "success" && query.state.data?.status === "setup_required" && query.state.data.check === "schema_update_running" ? 5000 : false,
  });
}
