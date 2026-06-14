/**
 * TanStack Query フック。query key を一元管理する。
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  api,
  type DatabaseSettingsUpdate,
  type EvaluationCompareRequestBody,
  type EvaluationRunRequestBody,
  type FileStatus,
  type ModelSettingsPayload,
  type ModelSettingsTestRequest,
  type SelectAiRequestBody,
  type UploadStorageSettingsUpdate,
} from "./api";

export const queryKeys = {
  authStatus: ["auth", "me"] as const,
  dashboardSummary: ["dashboard", "summary"] as const,
  documents: (params: { status?: FileStatus; q?: string; offset?: number }) =>
    ["documents", params] as const,
  document: (id: string) => ["documents", id] as const,
  documentStats: ["documents", "stats"] as const,
  modelSettings: ["settings", "model"] as const,
  databaseSettings: ["settings", "database"] as const,
  uploadStorageSettings: ["settings", "upload-storage"] as const,
};

/** ダッシュボード集計。 */
export function useDashboardSummary() {
  return useQuery({
    queryKey: queryKeys.dashboardSummary,
    queryFn: api.getDashboardSummary,
  });
}

/** ドキュメント一覧（ページング・絞り込み）。 */
export function useDocuments(params: {
  status?: FileStatus;
  q?: string;
  limit?: number;
  offset?: number;
}) {
  return useQuery({
    queryKey: queryKeys.documents({
      status: params.status,
      q: params.q,
      offset: params.offset,
    }),
    queryFn: () => api.listDocuments(params),
  });
}

/** ドキュメント詳細。 */
export function useDocument(id: string | null) {
  return useQuery({
    queryKey: queryKeys.document(id ?? ""),
    queryFn: () => api.getDocument(id as string),
    enabled: id != null,
  });
}

/** ファイルアップロード。成功時に一覧・ダッシュボードを無効化。 */
export function useUploadDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => api.uploadDocument(file),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboardSummary });
    },
  });
}

/** 取込（OCR/本文抽出→チャンク→埋め込み→索引）。 */
export function useIngestDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, force }: { id: string; force?: boolean }) =>
      api.ingestDocument(id, force),
    onSuccess: (detail) => {
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: queryKeys.document(detail.id) });
      qc.invalidateQueries({ queryKey: queryKeys.dashboardSummary });
    },
  });
}

/** Oracle Select AI。 */
export function useSelectAi() {
  return useMutation({
    mutationFn: (payload: SelectAiRequestBody) => api.selectAi(payload),
  });
}

/** RAG golden set 評価。 */
export function useRunEvaluation() {
  return useMutation({
    mutationFn: (payload: EvaluationRunRequestBody) => api.runEvaluation(payload),
  });
}

/** RAG 設定比較。 */
export function useCompareEvaluation() {
  return useMutation({
    mutationFn: (payload: EvaluationCompareRequestBody) => api.compareEvaluation(payload),
  });
}

/** モデル設定。 */
export function useModelSettings() {
  return useQuery({
    queryKey: queryKeys.modelSettings,
    queryFn: api.getModelSettings,
  });
}

/** モデル設定の保存。 */
export function useUpdateModelSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: ModelSettingsPayload) => api.updateModelSettings(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelSettings });
    },
  });
}

/** モデル設定の保存前チェック。 */
export function useCheckModelSettings() {
  return useMutation({
    mutationFn: (payload: ModelSettingsPayload) => api.checkModelSettings(payload),
  });
}

/** モデル単位の実 API テスト。 */
export function useTestModelSettings() {
  return useMutation({
    mutationFn: (payload: ModelSettingsTestRequest) => api.testModelSettings(payload),
  });
}

/** データベース設定。 */
export function useDatabaseSettings() {
  return useQuery({
    queryKey: queryKeys.databaseSettings,
    queryFn: api.getDatabaseSettings,
  });
}

/** データベース設定のランタイム保存。 */
export function useUpdateDatabaseSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: DatabaseSettingsUpdate) => api.updateDatabaseSettings(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.databaseSettings });
      qc.invalidateQueries({ queryKey: queryKeys.dashboardSummary });
    },
  });
}

/** Oracle Wallet ZIP のアップロード。 */
export function useUploadDatabaseWallet() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => api.uploadDatabaseWallet(file),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.databaseSettings });
      qc.invalidateQueries({ queryKey: queryKeys.dashboardSummary });
    },
  });
}

/** データベース接続テスト。 */
export function useTestDatabaseSettings() {
  return useMutation({
    mutationFn: (payload: DatabaseSettingsUpdate) => api.testDatabaseSettings(payload),
  });
}

/** アップロード原本の保存先設定。 */
export function useUploadStorageSettings() {
  return useQuery({
    queryKey: queryKeys.uploadStorageSettings,
    queryFn: api.getUploadStorageSettings,
  });
}

/** アップロード原本の保存先設定をランタイム保存。 */
export function useUpdateUploadStorageSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: UploadStorageSettingsUpdate) =>
      api.updateUploadStorageSettings(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.uploadStorageSettings });
      qc.invalidateQueries({ queryKey: queryKeys.dashboardSummary });
    },
  });
}
