/**
 * TanStack Query フック。query key を一元管理する。
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  api,
  type AdbSettingsUpdate,
  type DatabaseSettingsUpdate,
  type DocumentKnowledgeBaseReplaceRequest,
  type EvaluationCompareRequestBody,
  type EvaluationRunRequestBody,
  type FileStatus,
  type IngestionJobStatus,
  type KnowledgeBaseCreateRequest,
  type KnowledgeBaseStatus,
  type KnowledgeBaseUpdateRequest,
  type ModelSettingsPayload,
  type ModelSettingsTestRequest,
  type SelectAiRequestBody,
  type UploadIngestionMode,
  type UploadStorageSettingsUpdate,
} from "./api";

export const queryKeys = {
  authStatus: ["auth", "me"] as const,
  dashboardSummary: ["dashboard", "summary"] as const,
  documents: (params: {
    status?: FileStatus;
    q?: string;
    knowledge_base_id?: string;
    limit?: number;
    offset?: number;
  }) =>
    ["documents", params] as const,
  document: (id: string) => ["documents", id] as const,
  documentKnowledgeBases: (id: string) => ["documents", id, "knowledge-bases"] as const,
  documentStats: ["documents", "stats"] as const,
  ingestionJobs: (params: { status?: IngestionJobStatus; limit?: number; offset?: number }) =>
    ["documents", "ingestion-jobs", params] as const,
  knowledgeBases: (params: {
    status?: KnowledgeBaseStatus;
    q?: string;
    limit?: number;
    offset?: number;
  }) => ["knowledge-bases", params] as const,
  modelSettings: ["settings", "model"] as const,
  databaseSettings: ["settings", "database"] as const,
  adbInfo: ["settings", "database", "adb"] as const,
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
  knowledge_base_id?: string;
  limit?: number;
  offset?: number;
}) {
  return useQuery({
    queryKey: queryKeys.documents({
      status: params.status,
      q: params.q,
      knowledge_base_id: params.knowledge_base_id,
      limit: params.limit,
      offset: params.offset,
    }),
    queryFn: () => api.listDocuments(params),
  });
}

/** ドキュメント詳細。 */
export function useDocument(
  id: string | null,
  options: { refetchInterval?: number | false } = {}
) {
  return useQuery({
    queryKey: queryKeys.document(id ?? ""),
    queryFn: () => api.getDocument(id as string),
    enabled: id != null,
    refetchInterval: options.refetchInterval,
  });
}

/** 文書が所属するナレッジベース一覧。 */
export function useDocumentKnowledgeBases(id: string | null) {
  return useQuery({
    queryKey: queryKeys.documentKnowledgeBases(id ?? ""),
    queryFn: () => api.listDocumentKnowledgeBases(id as string),
    enabled: id != null,
  });
}

/** 文書のナレッジベース所属を置き換える。 */
export function useReplaceDocumentKnowledgeBases() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      payload,
    }: {
      id: string;
      payload: DocumentKnowledgeBaseReplaceRequest;
    }) => api.replaceDocumentKnowledgeBases(id, payload),
    onSuccess: (_refs, variables) => {
      qc.invalidateQueries({ queryKey: queryKeys.documentKnowledgeBases(variables.id) });
      qc.invalidateQueries({ queryKey: queryKeys.document(variables.id) });
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: ["knowledge-bases"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboardSummary });
    },
  });
}

/** ファイルアップロード。成功時に一覧・ダッシュボードを無効化。 */
export function useUploadDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      file,
      knowledgeBaseIds = [],
      ingestionMode = "manual",
    }: {
      file: File;
      knowledgeBaseIds?: string[];
      ingestionMode?: UploadIngestionMode;
    }) => api.uploadDocument(file, knowledgeBaseIds, ingestionMode),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: ["knowledge-bases"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboardSummary });
    },
  });
}

/** 複数ファイルアップロード。 */
export function useBatchUploadDocuments() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      files,
      knowledgeBaseIds = [],
      ingestionMode = "manual",
    }: {
      files: File[];
      knowledgeBaseIds?: string[];
      ingestionMode?: UploadIngestionMode;
    }) => api.batchUploadDocuments(files, knowledgeBaseIds, ingestionMode),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: ["knowledge-bases"] });
      qc.invalidateQueries({ queryKey: ["documents", "ingestion-jobs"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboardSummary });
    },
  });
}

/** 取込 job 一覧。 */
export function useIngestionJobs(params: {
  status?: IngestionJobStatus;
  limit?: number;
  offset?: number;
} = {}) {
  return useQuery({
    queryKey: queryKeys.ingestionJobs(params),
    queryFn: () => api.listIngestionJobs(params),
    refetchInterval: 3000,
  });
}

/** 取込 job 詳細。 */
export function useIngestionJob(id: string | null) {
  return useQuery({
    queryKey: ["documents", "ingestion-jobs", id] as const,
    queryFn: () => api.getIngestionJob(id as string),
    enabled: id != null,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "QUEUED" || status === "RUNNING" ? 2000 : false;
    },
  });
}

/** ナレッジベース一覧。 */
export function useKnowledgeBases(params: {
  status?: KnowledgeBaseStatus;
  q?: string;
  limit?: number;
  offset?: number;
}) {
  return useQuery({
    queryKey: queryKeys.knowledgeBases(params),
    queryFn: () => api.listKnowledgeBases(params),
  });
}

/** ナレッジベース作成。 */
export function useCreateKnowledgeBase() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: KnowledgeBaseCreateRequest) => api.createKnowledgeBase(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["knowledge-bases"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboardSummary });
    },
  });
}

/** ナレッジベース更新。 */
export function useUpdateKnowledgeBase() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: KnowledgeBaseUpdateRequest }) =>
      api.updateKnowledgeBase(id, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["knowledge-bases"] });
    },
  });
}

/** ナレッジベースをアーカイブする。 */
export function useArchiveKnowledgeBase() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.archiveKnowledgeBase(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["knowledge-bases"] });
      qc.invalidateQueries({ queryKey: ["documents"] });
    },
  });
}

/** 既存文書をナレッジベースへ追加する。 */
export function useAssignDocumentsToKnowledgeBase() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, documentIds }: { id: string; documentIds: string[] }) =>
      api.assignDocumentsToKnowledgeBase(id, { document_ids: documentIds }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["knowledge-bases"] });
      qc.invalidateQueries({ queryKey: ["documents"] });
    },
  });
}

/** 文書をナレッジベースから外す。 */
export function useRemoveDocumentFromKnowledgeBase() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ knowledgeBaseId, documentId }: { knowledgeBaseId: string; documentId: string }) =>
      api.removeDocumentFromKnowledgeBase(knowledgeBaseId, documentId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["knowledge-bases"] });
      qc.invalidateQueries({ queryKey: ["documents"] });
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

/** 文書を取込 job へ投入する。 */
export function useEnqueueDocumentIngestionJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, force }: { id: string; force?: boolean }) =>
      api.enqueueDocumentIngestionJob(id, force),
    onSuccess: (job) => {
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: queryKeys.document(job.document_id) });
      qc.invalidateQueries({ queryKey: ["documents", "ingestion-jobs"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboardSummary });
    },
  });
}

/** 永続化済み QUEUED job を再実行する。 */
export function useDrainIngestionJobs() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ limit = 50 }: { limit?: number } = {}) => api.drainIngestionJobs(limit),
    onSuccess: (jobs) => {
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: ["documents", "ingestion-jobs"] });
      for (const job of jobs) {
        qc.invalidateQueries({ queryKey: queryKeys.document(job.document_id) });
      }
      qc.invalidateQueries({ queryKey: queryKeys.dashboardSummary });
    },
  });
}

/** 失敗・完了済み job を新規 job として再投入する。 */
export function useRetryIngestionJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, force }: { id: string; force?: boolean }) =>
      api.retryIngestionJob(id, force),
    onSuccess: (job) => {
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: queryKeys.document(job.document_id) });
      qc.invalidateQueries({ queryKey: ["documents", "ingestion-jobs"] });
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

/** Autonomous Database 情報。 */
export function useAdbInfo() {
  return useQuery({
    queryKey: queryKeys.adbInfo,
    queryFn: api.getAdbInfo,
  });
}

/** ADB 操作対象 OCID / region の保存（最新情報を返す）。 */
export function useUpdateAdbSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: AdbSettingsUpdate) => api.updateAdbSettings(payload),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.adbInfo, data);
      qc.invalidateQueries({ queryKey: queryKeys.databaseSettings });
    },
  });
}

/** ADB 起動。 */
export function useStartAdb() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.startAdb(),
    onSuccess: (data) => qc.setQueryData(queryKeys.adbInfo, data),
  });
}

/** ADB 停止。 */
export function useStopAdb() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.stopAdb(),
    onSuccess: (data) => qc.setQueryData(queryKeys.adbInfo, data),
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
