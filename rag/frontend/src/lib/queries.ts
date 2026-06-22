/**
 * TanStack Query フック。query key を一元管理する。
 */

import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
  type QueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import {
  api,
  type AdbSettingsUpdate,
  type DashboardActivity,
  type DatabaseSettingsUpdate,
  type DocumentApproveRequest,
  type DocumentDetail,
  type DocumentSummary,
  type DocumentKnowledgeBaseReplaceRequest,
  type DocumentExtractionExportFormat,
  type EvaluationCompareRequestBody,
  type EvaluationRunRequestBody,
  type FileStatus,
  type IngestionJobStatus,
  type KnowledgeBaseCreateRequest,
  type KnowledgeBaseStatus,
  type KnowledgeBaseUpdateRequest,
  type BusinessViewCreateRequest,
  type BusinessViewStatus,
  type BusinessViewUpdateRequest,
  type ModelSettingsPayload,
  type ModelSettingsTestRequest,
  type ParserAdapterContractData,
  type ParserAdapterSettingsUpdate,
  type ParserAdapterSettingsData,
  type ChunkingSettingsData,
  type ChunkingSettingsUpdate,
  type PreprocessSettingsData,
  type PreprocessSettingsUpdate,
  type ServiceAction,
  type ServiceCatalogData,
  type ServiceControlResultData,
  type ServiceListData,
  type ServiceStatusData,
  type RetrievalSettingsData,
  type RetrievalSettingsUpdate,
  type GroundingSettingsData,
  type GroundingSettingsUpdate,
  type GenerationSettingsData,
  type GenerationSettingsUpdate,
  type GuardrailSettingsData,
  type GuardrailSettingsUpdate,
  type VectorIndexSettingsData,
  type VectorIndexSettingsUpdate,
  type EvaluationSettingsData,
  type EvaluationSettingsUpdate,
  type GraphSettingsData,
  type GraphSettingsUpdate,
  type AgenticSettingsData,
  type AgenticSettingsUpdate,
  type UploadIngestionMode,
  type UploadStorageSettingsUpdate,
} from "./api";

export const queryKeys = {
  authStatus: ["auth", "me"] as const,
  databaseStatus: ["system", "database-status"] as const,
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
  documentChunks: (id: string) => ["documents", id, "chunks"] as const,
  documentChunkSets: (id: string) => ["documents", id, "chunk-sets"] as const,
  documentExtractionExport: (id: string, format: DocumentExtractionExportFormat) =>
    ["documents", id, "extraction-export", format] as const,
  documentIngestionSegments: (id: string) =>
    ["documents", id, "ingestion-segments"] as const,
  documentIngestionConfig: (id: string) =>
    ["documents", id, "ingestion-config"] as const,
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
  knowledgeBase: (id: string) => ["knowledge-bases", id] as const,
  businessViews: (params: {
    status?: BusinessViewStatus;
    q?: string;
    limit?: number;
    offset?: number;
  }) => ["business-views", params] as const,
  businessView: (id: string) => ["business-views", id] as const,
  modelSettings: ["settings", "model"] as const,
  databaseSettings: ["settings", "database"] as const,
  adbInfo: ["settings", "database", "adb"] as const,
  uploadStorageSettings: ["settings", "upload-storage"] as const,
  parserAdapterSettings: ["settings", "parser-adapters"] as const,
  parserAdapterContract: ["settings", "parser-adapters", "contract"] as const,
  preprocessSettings: ["settings", "preprocess"] as const,
  chunkingSettings: ["settings", "chunking"] as const,
  retrievalSettings: ["settings", "retrieval"] as const,
  groundingSettings: ["settings", "grounding"] as const,
  generationSettings: ["settings", "generation"] as const,
  guardrailSettings: ["settings", "guardrail"] as const,
  vectorIndexSettings: ["settings", "vector-index"] as const,
  evaluationSettings: ["settings", "evaluation-suite"] as const,
  graphSettings: ["settings", "graph"] as const,
  agenticSettings: ["settings", "agentic"] as const,
  services: ["services"] as const,
  serviceCatalog: ["services", "catalog"] as const,
  serviceStatus: (serviceId: string) => ["services", "status", serviceId] as const,
};

const DOCUMENT_EXTRACTION_EXPORT_FORMATS: DocumentExtractionExportFormat[] = [
  "markdown",
  "html",
  "json",
  "chunks",
];

function clearDocumentProcessingCache(qc: QueryClient, documentId: string) {
  qc.setQueryData<DocumentDetail | undefined>(queryKeys.document(documentId), (current) =>
    current
      ? {
          ...current,
          status: "UPLOADED",
          extraction: {},
          error_message: null,
          indexed_at: null,
        }
      : current
  );
  qc.setQueryData(queryKeys.documentChunks(documentId), []);
  qc.setQueryData(queryKeys.documentIngestionSegments(documentId), []);
  qc.removeQueries({ queryKey: queryKeys.documentChunkSets(documentId) });
  for (const format of DOCUMENT_EXTRACTION_EXPORT_FORMATS) {
    qc.removeQueries({ queryKey: queryKeys.documentExtractionExport(documentId, format) });
  }
}

function invalidateDocumentProcessingQueries(qc: QueryClient, documentId: string) {
  qc.invalidateQueries({ queryKey: ["documents"] });
  qc.invalidateQueries({ queryKey: queryKeys.document(documentId) });
  qc.invalidateQueries({ queryKey: queryKeys.documentChunks(documentId) });
  qc.invalidateQueries({ queryKey: queryKeys.documentChunkSets(documentId) });
  qc.invalidateQueries({ queryKey: ["documents", documentId, "extraction-export"] });
  qc.invalidateQueries({ queryKey: queryKeys.documentIngestionSegments(documentId) });
  qc.invalidateQueries({ queryKey: ["documents", "ingestion-jobs"] });
  qc.invalidateQueries({ queryKey: queryKeys.dashboardSummary });
}

/**
 * 自動更新(条件付きポーリング)の遷移状態判定。
 *
 * `refetchInterval` を関数形式で使い、状態遷移中だけポーリングして安定/終了したら
 * 止める。`useDocumentIngestionSegments` / `useIngestionJob` と同じ方針。純粋関数
 * として切り出し Vitest で境界を検証する。
 */

/** 取込/索引が進行中で一覧を再取得すべき文書状態。 */
export const DOCUMENT_ACTIVE_STATUSES: ReadonlySet<FileStatus> = new Set<FileStatus>([
  "INGESTING",
  "INDEXING",
]);

/** ADB が起動/停止などの遷移中で lifecycle を再取得すべき状態。 */
export const ADB_TRANSITIONAL_STATES: ReadonlySet<string> = new Set<string>([
  "STARTING",
  "STOPPING",
  "PROVISIONING",
  "TERMINATING",
  "UPDATING",
  "RESTORING",
  "BACKUP_IN_PROGRESS",
  "MAINTENANCE_IN_PROGRESS",
  "ROLE_CHANGE_IN_PROGRESS",
]);

/** ポーリング間隔(ms)。 */
export const ACTIVE_REFETCH_INTERVAL_MS = 4000;

/** 文書一覧に取込/索引進行中の文書が含まれるか。 */
export function documentsHaveActiveWork(
  items: ReadonlyArray<Pick<DocumentSummary, "status">> | undefined
): boolean {
  return Boolean(items?.some((item) => DOCUMENT_ACTIVE_STATUSES.has(item.status)));
}

/** ADB lifecycle が遷移中か。 */
export function adbIsTransitioning(state: string | null | undefined): boolean {
  return state != null && ADB_TRANSITIONAL_STATES.has(state);
}

/** ダッシュボードの最近のアクティビティに進行中の処理が含まれるか。 */
export function dashboardHasActiveWork(
  activities: ReadonlyArray<Pick<DashboardActivity, "status">> | undefined
): boolean {
  return Boolean(activities?.some((activity) => DOCUMENT_ACTIVE_STATUSES.has(activity.status)));
}

/** 取込 job がまだキュー待ち/実行中か。 */
export function ingestionJobIsActive(
  status: IngestionJobStatus | null | undefined
): boolean {
  return status === "QUEUED" || status === "RUNNING";
}

/** 取込 segment がまだキュー待ち/実行中か。 */
export function ingestionSegmentHasActiveWork(
  segments: ReadonlyArray<{ status: string }> | undefined
): boolean {
  return Boolean(
    segments?.some((segment) => segment.status === "QUEUED" || segment.status === "RUNNING")
  );
}

export function documentWorkspaceShouldRefresh({
  documentStatus,
  watchProcessing = false,
  localWatchProcessing = false,
  jobStatuses = [],
  segmentStatuses = [],
}: {
  documentStatus: FileStatus | null | undefined;
  watchProcessing?: boolean;
  localWatchProcessing?: boolean;
  jobStatuses?: ReadonlyArray<IngestionJobStatus | null | undefined>;
  segmentStatuses?: ReadonlyArray<string | null | undefined>;
}): boolean {
  if (jobStatuses.some(ingestionJobIsActive)) return true;
  if (segmentStatuses.some((status) => status === "QUEUED" || status === "RUNNING")) return true;
  if (documentStatus != null && DOCUMENT_ACTIVE_STATUSES.has(documentStatus)) return true;

  // REVIEW / INDEXED / ERROR は通常は安定状態。ただし job 投入直後の引き継ぎ窓では
  // mutation 側の job status が上の判定に入るため、ここでは止めてよい。
  const terminal =
    documentStatus === "INDEXED" || documentStatus === "ERROR" || documentStatus === "REVIEW";
  return Boolean((watchProcessing || localWatchProcessing) && !terminal);
}

/** データベース利用可否(DB ゲート用)。設定ページ以外を開く前に参照する。 */
export function useDatabaseStatus(options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: queryKeys.databaseStatus,
    queryFn: api.getDatabaseStatus,
    enabled: options.enabled ?? true,
    retry: false,
    // 短時間はキャッシュし、ページ遷移ごとの再プローブを避ける。
    staleTime: 15_000,
  });
}

/** ダッシュボード集計。取込/索引が進行中の間だけ自動再取得する。 */
export function useDashboardSummary() {
  return useQuery({
    queryKey: queryKeys.dashboardSummary,
    queryFn: api.getDashboardSummary,
    retry: false,
    refetchInterval: (query) =>
      dashboardHasActiveWork(query.state.data?.recent_activities)
        ? ACTIVE_REFETCH_INTERVAL_MS
        : false,
  });
}

/**
 * ドキュメント一覧（ページング・絞り込み）。
 *
 * 取込/索引が進行中の文書が 1 件でもある間は自動再取得して状態バッジを更新する。
 * `graceActive` は「取込ジョブ投入直後の UPLOADED→INGESTING 引き継ぎ窓」で、まだ
 * アクティブ状態が現れていない間もポーリングを継続させるためにコンポーネントが渡す。
 */
export function useDocuments(
  params: {
    status?: FileStatus;
    q?: string;
    knowledge_base_id?: string;
    limit?: number;
    offset?: number;
  },
  options: { graceActive?: boolean } = {}
) {
  return useQuery({
    queryKey: queryKeys.documents({
      status: params.status,
      q: params.q,
      knowledge_base_id: params.knowledge_base_id,
      limit: params.limit,
      offset: params.offset,
    }),
    queryFn: () => api.listDocuments(params),
    refetchInterval: (query) =>
      documentsHaveActiveWork(query.state.data?.items) || options.graceActive
        ? ACTIVE_REFETCH_INTERVAL_MS
        : false,
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

/** 文書 chunk/citation 可視化。 */
export function useDocumentChunks(id: string | null) {
  return useQuery({
    queryKey: queryKeys.documentChunks(id ?? ""),
    queryFn: () => api.listDocumentChunks(id as string),
    enabled: id != null,
    retry: false,
  });
}

/** 文書の chunk_set(variant)一覧。展開時のみ lazy 取得する。 */
export function useDocumentChunkSets(id: string | null, enabled = true) {
  return useQuery({
    queryKey: queryKeys.documentChunkSets(id ?? ""),
    queryFn: () => api.listDocumentChunkSets(id as string),
    enabled: id != null && enabled,
    retry: false,
  });
}

/** 文書 extraction の監査用 export view。 */
export function useDocumentExtractionExport(
  id: string | null,
  format: DocumentExtractionExportFormat
) {
  return useQuery({
    queryKey: queryKeys.documentExtractionExport(id ?? "", format),
    queryFn: () => api.exportDocumentExtraction(id as string, format),
    enabled: id != null,
    retry: false,
  });
}

/** 文書取込 segment/checkpoint 状態。 */
export function useDocumentIngestionSegments(id: string | null) {
  return useQuery({
    queryKey: queryKeys.documentIngestionSegments(id ?? ""),
    queryFn: () => api.listDocumentIngestionSegments(id as string),
    enabled: id != null,
    retry: false,
    refetchInterval: (query) =>
      ingestionSegmentHasActiveWork(query.state.data) ? 2000 : false,
  });
}

/** 失敗した segment checkpoint のみを再試行する取込 job を投入する。 */
export function useRetryFailedDocumentIngestionSegments() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.retryFailedDocumentIngestionSegments(id),
    onSuccess: (job, documentId) => {
      qc.invalidateQueries({ queryKey: queryKeys.document(documentId) });
      qc.invalidateQueries({ queryKey: queryKeys.documentIngestionSegments(documentId) });
      qc.invalidateQueries({ queryKey: ["documents", "ingestion-jobs"] });
      qc.invalidateQueries({ queryKey: ["documents", "ingestion-jobs", job.id] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboardSummary });
    },
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

/** 文書の取込設定スナップショットと owning KB とのドリフト状況。 */
export function useDocumentIngestionConfig(id: string | null) {
  return useQuery({
    queryKey: queryKeys.documentIngestionConfig(id ?? ""),
    queryFn: () => api.getDocumentIngestionConfig(id as string),
    enabled: id != null,
    // 404(削除済み/未登録の文書)はリトライしても無意味。兄弟の文書スコープ
    // クエリ(chunks / extraction-export / ingestion-segments)と挙動を揃える。
    retry: false,
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

/** ドキュメント本体と検索 index を削除する。 */
export function useDeleteDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteDocument(id),
    onSuccess: (result) => {
      qc.removeQueries({ queryKey: queryKeys.document(result.id) });
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: ["documents", "ingestion-jobs"] });
      qc.invalidateQueries({ queryKey: ["knowledge-bases"] });
      qc.invalidateQueries({ queryKey: queryKeys.documentStats });
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
    refetchInterval: (query) => (ingestionJobIsActive(query.state.data?.status) ? 2000 : false),
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

/** ナレッジベース詳細(adapter_config を含む)。 */
export function useKnowledgeBase(id: string | null) {
  return useQuery({
    queryKey: queryKeys.knowledgeBase(id ?? ""),
    queryFn: () => api.getKnowledgeBase(id as string),
    enabled: id != null,
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

/** 業務ビュー(Business View)一覧。 */
export function useBusinessViews(params: {
  status?: BusinessViewStatus;
  q?: string;
  limit?: number;
  offset?: number;
}) {
  return useQuery({
    queryKey: queryKeys.businessViews(params),
    queryFn: () => api.listBusinessViews(params),
  });
}

/** 業務ビュー詳細(config・参照 KB を含む)。 */
export function useBusinessView(id: string | null) {
  return useQuery({
    queryKey: queryKeys.businessView(id ?? ""),
    queryFn: () => api.getBusinessView(id as string),
    enabled: id != null,
  });
}

/** 業務ビュー作成。 */
export function useCreateBusinessView() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: BusinessViewCreateRequest) => api.createBusinessView(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["business-views"] });
    },
  });
}

/** 業務ビュー更新。 */
export function useUpdateBusinessView() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: BusinessViewUpdateRequest }) =>
      api.updateBusinessView(id, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["business-views"] });
    },
  });
}

/** 業務ビューをアーカイブする。 */
export function useArchiveBusinessView() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.archiveBusinessView(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["business-views"] });
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
      api.enqueueDocumentIngestionJob(id, force),
    onSuccess: (job) => {
      if (job.phase === "EXTRACT" && job.status === "QUEUED") {
        clearDocumentProcessingCache(qc, job.document_id);
      }
      invalidateDocumentProcessingQueries(qc, job.document_id);
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
      if (job.phase === "EXTRACT" && job.status === "QUEUED") {
        clearDocumentProcessingCache(qc, job.document_id);
      }
      invalidateDocumentProcessingQueries(qc, job.document_id);
    },
  });
}

/** REVIEW(確認待ち)文書を承認し、後段 index を投入する。任意でテキスト修正を伴う。 */
export function useApproveDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload?: DocumentApproveRequest }) =>
      api.approveDocument(id, payload),
    onSuccess: (job) => {
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: queryKeys.document(job.document_id) });
      qc.invalidateQueries({ queryKey: queryKeys.documentChunkSets(job.document_id) });
      qc.invalidateQueries({ queryKey: queryKeys.documentIngestionSegments(job.document_id) });
      qc.invalidateQueries({ queryKey: ["documents", "ingestion-jobs"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboardSummary });
    },
  });
}

/** REVIEW(確認待ち)文書を却下し、UPLOADED へ戻す。 */
export function useRejectDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id }: { id: string }) => api.rejectDocument(id),
    onSuccess: (detail) => {
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: queryKeys.document(detail.id) });
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
      qc.invalidateQueries({ queryKey: queryKeys.documentIngestionSegments(job.document_id) });
      qc.invalidateQueries({ queryKey: ["documents", "ingestion-jobs"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboardSummary });
    },
  });
}

/** 待機中・実行中の取込 job をキャンセルする。 */
export function useCancelIngestionJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id }: { id: string }) => api.cancelIngestionJob(id),
    onSuccess: (job) => {
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: queryKeys.document(job.document_id) });
      qc.invalidateQueries({ queryKey: ["documents", "ingestion-jobs"] });
      qc.invalidateQueries({ queryKey: queryKeys.dashboardSummary });
    },
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

/** Autonomous Database 情報。起動/停止などの遷移中だけ自動再取得する。 */
export function useAdbInfo() {
  return useQuery({
    queryKey: queryKeys.adbInfo,
    queryFn: api.getAdbInfo,
    refetchInterval: (query) =>
      adbIsTransitioning(query.state.data?.lifecycle_state)
        ? ACTIVE_REFETCH_INTERVAL_MS
        : false,
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

/** 任意 parser adapter の runtime readiness。 */
export function useParserAdapterSettings() {
  return useQuery<ParserAdapterSettingsData>({
    queryKey: queryKeys.parserAdapterSettings,
    queryFn: api.getParserAdapterSettings,
    retry: false,
  });
}

/** 任意 parser adapter の schema remap compatibility matrix。 */
export function useParserAdapterContract() {
  return useQuery<ParserAdapterContractData>({
    queryKey: queryKeys.parserAdapterContract,
    queryFn: api.getParserAdapterContract,
    enabled: false,
    retry: false,
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

/** GraphRAG アダプター(知識グラフ構築)の runtime 設定。 */
export function useGraphSettings() {
  return useQuery<GraphSettingsData>({
    queryKey: queryKeys.graphSettings,
    queryFn: api.getGraphSettings,
    retry: false,
  });
}

/** GraphRAG アダプター設定をランタイム保存。 */
export function useUpdateGraphSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: GraphSettingsUpdate) => api.updateGraphSettings(payload),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.graphSettings, data);
      qc.invalidateQueries({ queryKey: queryKeys.dashboardSummary });
    },
  });
}

/** Agentic アダプター(クエリ計画)の runtime 設定。 */
export function useAgenticSettings() {
  return useQuery<AgenticSettingsData>({
    queryKey: queryKeys.agenticSettings,
    queryFn: api.getAgenticSettings,
    retry: false,
  });
}

/** Agentic アダプター設定をランタイム保存。 */
export function useUpdateAgenticSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: AgenticSettingsUpdate) => api.updateAgenticSettings(payload),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.agenticSettings, data);
      qc.invalidateQueries({ queryKey: queryKeys.dashboardSummary });
    },
  });
}

/** Evaluation アダプター(評価スイート/閾値)の runtime 設定。 */
export function useEvaluationSettings() {
  return useQuery<EvaluationSettingsData>({
    queryKey: queryKeys.evaluationSettings,
    queryFn: api.getEvaluationSettings,
    retry: false,
  });
}

/** Evaluation アダプター設定をランタイム保存。 */
export function useUpdateEvaluationSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: EvaluationSettingsUpdate) => api.updateEvaluationSettings(payload),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.evaluationSettings, data);
    },
  });
}

/** Vector Index アダプター(索引/検索精度)の runtime 設定。 */
export function useVectorIndexSettings() {
  return useQuery<VectorIndexSettingsData>({
    queryKey: queryKeys.vectorIndexSettings,
    queryFn: api.getVectorIndexSettings,
    retry: false,
  });
}

/** Vector Index アダプター設定をランタイム保存。 */
export function useUpdateVectorIndexSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: VectorIndexSettingsUpdate) => api.updateVectorIndexSettings(payload),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.vectorIndexSettings, data);
      qc.invalidateQueries({ queryKey: queryKeys.dashboardSummary });
    },
  });
}

/** Generation アダプター(回答生成)の runtime 設定。 */
export function useGenerationSettings() {
  return useQuery<GenerationSettingsData>({
    queryKey: queryKeys.generationSettings,
    queryFn: api.getGenerationSettings,
    retry: false,
  });
}

/** Generation アダプター設定をランタイム保存。 */
export function useUpdateGenerationSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: GenerationSettingsUpdate) => api.updateGenerationSettings(payload),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.generationSettings, data);
      qc.invalidateQueries({ queryKey: queryKeys.dashboardSummary });
    },
  });
}

/** Guardrail アダプター(安全)の runtime 設定。 */
export function useGuardrailSettings() {
  return useQuery<GuardrailSettingsData>({
    queryKey: queryKeys.guardrailSettings,
    queryFn: api.getGuardrailSettings,
    retry: false,
  });
}

/** Guardrail アダプター設定をランタイム保存。 */
export function useUpdateGuardrailSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: GuardrailSettingsUpdate) => api.updateGuardrailSettings(payload),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.guardrailSettings, data);
      qc.invalidateQueries({ queryKey: queryKeys.dashboardSummary });
    },
  });
}

/** Retrieval アダプター(検索戦略)の runtime 設定。 */
export function useRetrievalSettings() {
  return useQuery<RetrievalSettingsData>({
    queryKey: queryKeys.retrievalSettings,
    queryFn: api.getRetrievalSettings,
    retry: false,
  });
}

/** Retrieval アダプター設定をランタイム保存。 */
export function useUpdateRetrievalSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: RetrievalSettingsUpdate) => api.updateRetrievalSettings(payload),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.retrievalSettings, data);
      qc.invalidateQueries({ queryKey: queryKeys.dashboardSummary });
    },
  });
}

/** Grounding アダプター(検索後処理)の runtime 設定。 */
export function useGroundingSettings() {
  return useQuery<GroundingSettingsData>({
    queryKey: queryKeys.groundingSettings,
    queryFn: api.getGroundingSettings,
    retry: false,
  });
}

/** Grounding アダプター設定をランタイム保存。 */
export function useUpdateGroundingSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: GroundingSettingsUpdate) => api.updateGroundingSettings(payload),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.groundingSettings, data);
      qc.invalidateQueries({ queryKey: queryKeys.dashboardSummary });
    },
  });
}

/** Chunking アダプター(分割戦略)の runtime 設定。 */
export function usePreprocessSettings() {
  return useQuery<PreprocessSettingsData>({
    queryKey: queryKeys.preprocessSettings,
    queryFn: api.getPreprocessSettings,
    retry: false,
  });
}

/** マイクロサービス一覧の静的メタデータ。稼働プローブは行わず、画面初期表示を軽くする。 */
export function useServiceCatalog(options: { refetchInterval?: number | false } = {}) {
  return useQuery<ServiceCatalogData>({
    queryKey: queryKeys.serviceCatalog,
    queryFn: api.getServiceCatalog,
    retry: false,
    refetchInterval: options.refetchInterval ?? false,
  });
}

/** マイクロサービスの稼働状態をサービス単位で取得する。 */
export function useServiceStatusQueries(
  serviceIds: string[],
  options: { refetchInterval?: number | false } = {}
): UseQueryResult<ServiceStatusData>[] {
  return useQueries({
    queries: serviceIds.map((serviceId) => ({
      queryKey: queryKeys.serviceStatus(serviceId),
      queryFn: () => api.getServiceStatus(serviceId),
      retry: false,
      refetchInterval: options.refetchInterval ?? 5000,
    })),
  }) as UseQueryResult<ServiceStatusData>[];
}

/** マイクロサービスの稼働状態一覧。既定 5s でポーリングして稼働状況をライブ表示する。 */
export function useServices(options: { refetchInterval?: number | false } = {}) {
  return useQuery<ServiceListData>({
    queryKey: queryKeys.services,
    queryFn: api.getServices,
    retry: false,
    refetchInterval: options.refetchInterval ?? 5000,
  });
}

/** サービスを起動/停止/再起動する(成功時に一覧を即時 invalidate)。 */
export function useControlService() {
  const qc = useQueryClient();
  return useMutation<
    ServiceControlResultData,
    unknown,
    { serviceId: string; action: ServiceAction }
  >({
    mutationFn: ({ serviceId, action }) => api.controlService(serviceId, action),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.services });
    },
  });
}

/** 前処理(Preprocess)アダプター設定をランタイム保存。 */
export function useUpdatePreprocessSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: PreprocessSettingsUpdate) => api.updatePreprocessSettings(payload),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.preprocessSettings, data);
      qc.invalidateQueries({ queryKey: queryKeys.dashboardSummary });
    },
  });
}

export function useChunkingSettings() {
  return useQuery<ChunkingSettingsData>({
    queryKey: queryKeys.chunkingSettings,
    queryFn: api.getChunkingSettings,
    retry: false,
  });
}

/** Chunking アダプター設定をランタイム保存。 */
export function useUpdateChunkingSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: ChunkingSettingsUpdate) => api.updateChunkingSettings(payload),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.chunkingSettings, data);
      qc.invalidateQueries({ queryKey: queryKeys.dashboardSummary });
    },
  });
}

/** 任意 parser adapter 設定をランタイム保存。 */
export function useUpdateParserAdapterSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: ParserAdapterSettingsUpdate) =>
      api.updateParserAdapterSettings(payload),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.parserAdapterSettings, data);
      qc.invalidateQueries({ queryKey: queryKeys.parserAdapterContract });
      qc.invalidateQueries({ queryKey: queryKeys.dashboardSummary });
    },
  });
}
