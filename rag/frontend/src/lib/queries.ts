/**
 * TanStack Query フック。query key を一元管理する。
 */

import {
  DATABASE_STATUS_QUERY_KEY,
  SYSTEM_TABLES_QUERY_KEY,
} from "@engchina/production-ready-system-settings";
import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
  type QueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import {
  ANSWER_TRACE_ID_FILTER_MAX,
  EVALUATION_JOB_POLL_INTERVAL_MS,
  api,
  ApiError,
  type BatchUploadFailedItem,
  type BatchUploadResult,
  type KnowledgeBaseSummary,
  type HuggingFaceSettingsUpdate,
  type DocumentApproveRequest,
  type DocumentChunkPreviewRequest,
  type DocumentReviewEditsRequest,
  type DocumentDetail,
  type DocumentSummary,
  type DocumentClassification,
  type DocragPromptKey,
  type QueryHistorySettingsData,
  type DocumentKnowledgeBaseReplaceRequest,
  type DocumentProcessingConfig,
  type DocumentExtractionExportFormat,
  type EvaluationCompareRequestBody,
  type EvaluationJob,
  type EvaluationRunRequestBody,
  type ExternalParserBackendName,
  type ExternalParserConnectionStatusData,
  type FileStatus,
  type FeedbackListParams,
  type FeedbackRequestBody,
  type IngestionJobPhase,
  type IngestionJobStatus,
  type KnowledgeBaseCreateRequest,
  type KnowledgeBaseStatus,
  type KnowledgeBaseUpdateRequest,
  type BusinessViewCreateRequest,
  type BusinessViewStatus,
  type BusinessViewUpdateRequest,
  type ConversationCreateBody,
  type ConversationUpdateBody,
  type ParserAdapterSettingsUpdate,
  type ParserAdapterSettingsData,
  type ChunkingSettingsData,
  type ChunkingSettingsUpdate,
  type PreprocessSettingsData,
  type PreprocessSettingsUpdate,
  type ServiceAction,
  type ServiceCatalogData,
  type ServiceControlResultData,
  type ServiceLogsData,
  type ServiceStatusData,
  type RetrievalSettingsData,
  type RetrievalSettingsUpdate,
  type GroundingSettingsData,
  type GroundingSettingsUpdate,
  type GenerationSettingsData,
  type GenerationSettingsUpdate,
  type PromptVersionsData,
  type PromptVersionCreate,
  type ExtractionFieldDefinition,
  type ExtractionFieldsSettingsData,
  type KnowledgeBaseExtractionFieldsData,
  type PipelineSettingsData,
  type PipelineSettingsUpdate,
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
  type ApprovedFaqMutationData,
  type RuntimeKnowledgeEditRequest,
} from "./api";
import { t } from "./i18n";
import {
  DEFAULT_MAX_UPLOAD_BYTES,
  failedUploadItem,
  formatByteSize,
  mergeBatchUploadResults,
  planUploadRequests,
  totalUploadBytes,
  uploadProgressOf,
  type UploadProgress,
} from "./upload-requests";

export const queryKeys = {
  // DB の状態は3製品共通の query key（#325）。
  databaseStatus: DATABASE_STATUS_QUERY_KEY,
  documents: (params: {
    status?: FileStatus;
    q?: string;
    knowledge_base_id?: string;
    limit?: number;
    offset?: number;
  }) => ["documents", params] as const,
  document: (id: string) => ["documents", id] as const,
  documentClassificationOptions: ["document-classification-options"] as const,
  documentChunkSets: (id: string) => ["documents", id, "chunk-sets"] as const,
  documentRecipes: (id: string) => ["documents", id, "recipes"] as const,
  documentPreviewPages: (id: string, recipeId: string | null, variant: string) =>
    ["documents", id, "preview-pages", recipeId ?? "", variant] as const,
  documentRecipeChunks: (id: string, recipeId: string) =>
    ["documents", id, "recipes", recipeId, "chunks"] as const,
  documentRecipeExtractionExport: (
    id: string,
    recipeId: string,
    format: DocumentExtractionExportFormat,
  ) =>
    [
      "documents",
      id,
      "recipes",
      recipeId,
      "extraction-export",
      format,
    ] as const,
  documentIngestionJobs: (id: string) =>
    ["documents", id, "ingestion-jobs"] as const,
  documentIngestionSegments: (id: string) =>
    ["documents", id, "ingestion-segments"] as const,
  documentKnowledgeBases: (id: string) =>
    ["documents", id, "knowledge-bases"] as const,
  ingestionJobs: (params: {
    status?: IngestionJobStatus;
    limit?: number;
    offset?: number;
  }) => ["documents", "ingestion-jobs", params] as const,
  knowledgeBases: (params: {
    status?: KnowledgeBaseStatus;
    q?: string;
    limit?: number;
    offset?: number;
    ids?: string[];
  }) => ["knowledge-bases", params] as const,
  knowledgeBaseSearch: (params: { status?: KnowledgeBaseStatus; q?: string }) =>
    ["knowledge-bases", "search", params] as const,
  knowledgeBase: (id: string) => ["knowledge-bases", id] as const,
  knowledgeBaseGraph: (id: string) => ["knowledge-bases", id, "graph"] as const,
  knowledgeBaseExtractionFields: (id: string) =>
    ["knowledge-bases", id, "extraction-fields"] as const,
  searchExtractionFields: (businessViewIds: string[]) =>
    ["search", "extraction-fields", businessViewIds] as const,
  businessViews: (params: {
    status?: BusinessViewStatus;
    q?: string;
    limit?: number;
    offset?: number;
  }) => ["business-views", params] as const,
  businessView: (id: string) => ["business-views", id] as const,
  conversations: (params: {
    business_view_id?: string;
    limit?: number;
    offset?: number;
  }) => ["conversations", params] as const,
  conversation: (id: string) => ["conversations", id] as const,
  currentFeedback: (traceId: string) =>
    ["feedback", "current", traceId] as const,
  feedback: (params: FeedbackListParams) => ["feedback", params] as const,
  feedbackDetail: (id: string) => ["feedback", "detail", id] as const,
  compareModels: ["chat", "models"] as const,
  modelSettings: ["settings", "model"] as const,
  databaseSettings: ["settings", "database"] as const,
  systemTables: SYSTEM_TABLES_QUERY_KEY,
  adbInfo: ["settings", "database", "adb"] as const,
  huggingfaceSettings: ["settings", "huggingface"] as const,
  uploadStorageSettings: ["settings", "upload-storage"] as const,
  parserAdapterSettings: ["settings", "parser-adapters"] as const,
  externalParserStatuses: ["settings", "parser-adapters", "status"] as const,
  externalParserStatus: (backend: ExternalParserBackendName) =>
    ["settings", "parser-adapters", "status", backend] as const,
  preprocessSettings: ["settings", "preprocess"] as const,
  chunkingSettings: ["settings", "chunking"] as const,
  retrievalSettings: ["settings", "retrieval"] as const,
  groundingSettings: ["settings", "grounding"] as const,
  generationSettings: ["settings", "generation"] as const,
  promptVersions: ["settings", "prompts"] as const,
  guardrailSettings: ["settings", "guardrail"] as const,
  extractionFieldsSettings: ["settings", "extraction-fields"] as const,
  pipelineSettings: ["settings", "pipeline"] as const,
  vectorIndexSettings: ["settings", "vector-index"] as const,
  evaluationSettings: ["settings", "evaluation-suite"] as const,
  graphSettings: ["settings", "graph"] as const,
  agenticSettings: ["settings", "agentic"] as const,
  services: ["services"] as const,
  serviceCatalog: ["services", "catalog"] as const,
  serviceStatus: (serviceId: string) =>
    ["services", "status", serviceId] as const,
  serviceLogs: (serviceId: string, lines: number) =>
    ["services", "logs", serviceId, lines] as const,
};

function clearDocumentProcessingCache(
  qc: QueryClient,
  documentId: string,
  options: { clearPreprocessArtifact?: boolean } = {},
) {
  qc.setQueryData<DocumentDetail | undefined>(
    queryKeys.document(documentId),
    (current) =>
      current
        ? {
            ...current,
            status: "UPLOADED",
            preprocess_artifact: options.clearPreprocessArtifact
              ? null
              : current.preprocess_artifact,
            extraction: {},
            error_message: null,
            indexed_at: null,
          }
        : current,
  );
  qc.setQueryData(queryKeys.documentIngestionSegments(documentId), []);
  qc.removeQueries({ queryKey: queryKeys.documentChunkSets(documentId) });
}

function invalidateDocumentProcessingQueries(
  qc: QueryClient,
  documentId: string,
) {
  qc.invalidateQueries({ queryKey: ["documents"] });
  qc.invalidateQueries({ queryKey: queryKeys.document(documentId) });
  qc.invalidateQueries({ queryKey: queryKeys.documentChunkSets(documentId) });
  qc.invalidateQueries({
    queryKey: queryKeys.documentIngestionJobs(documentId),
  });
  qc.invalidateQueries({
    queryKey: queryKeys.documentIngestionSegments(documentId),
  });
  qc.invalidateQueries({ queryKey: ["documents", "ingestion-jobs"] });
}

/**
 * 自動更新(条件付きポーリング)の遷移状態判定。
 *
 * `refetchInterval` を関数形式で使い、状態遷移中だけポーリングして安定/終了したら
 * 止める。純粋関数として切り出し Vitest で境界を検証する。
 */

/** 取込/索引が進行中で一覧を再取得すべき文書状態。 */
export const DOCUMENT_ACTIVE_STATUSES: ReadonlySet<FileStatus> =
  new Set<FileStatus>(["PREPROCESSING", "INGESTING", "CHUNKING", "INDEXING"]);

/** ポーリング間隔(ms)。 */
export const ACTIVE_REFETCH_INTERVAL_MS = 4000;

/** 文書一覧に取込/索引進行中の文書が含まれるか。 */
export function documentsHaveActiveWork(
  items: ReadonlyArray<Pick<DocumentSummary, "status">> | undefined,
): boolean {
  return Boolean(
    items?.some((item) => DOCUMENT_ACTIVE_STATUSES.has(item.status)),
  );
}

/** 取込 job がまだキュー待ち/実行中か。 */
export function ingestionJobIsActive(
  status: IngestionJobStatus | null | undefined,
): boolean {
  return status === "QUEUED" || status === "RUNNING";
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
  if (
    segmentStatuses.some(
      (status) => status === "QUEUED" || status === "RUNNING",
    )
  )
    return true;
  if (documentStatus != null && DOCUMENT_ACTIVE_STATUSES.has(documentStatus))
    return true;

  // PREPROCESSED / REVIEW / CHUNKED / INDEXED / ERROR は通常は安定状態。ただし job 投入直後の
  // 引き継ぎ窓では mutation 側の job status が上の判定に入るため、ここでは止めてよい。
  const terminal =
    documentStatus === "INDEXED" ||
    documentStatus === "ERROR" ||
    documentStatus === "PREPROCESSED" ||
    documentStatus === "REVIEW";
  const stageReview = documentStatus === "CHUNKED";
  return Boolean(
    (watchProcessing || localWatchProcessing) && !terminal && !stageReview,
  );
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
  options: { graceActive?: boolean } = {},
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
    // 検索語・絞り込み・ページを変えている間は、前の一覧を出したまま取り直す（#535。useKnowledgeBases と同じ）。
    placeholderData: keepPreviousData,
    refetchInterval: (query) =>
      documentsHaveActiveWork(query.state.data?.items) || options.graceActive
        ? ACTIVE_REFETCH_INTERVAL_MS
        : false,
  });
}

/** 404 以外の失敗だけを最大 3 回まで再試行する(TanStack Query の既定の回数に合わせる)。 */
export function retryUnlessNotFound(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status === 404) return false;
  return failureCount < 3;
}

/**
 * 初回の取得に失敗したときだけエラーを返す(#311)。
 * データがあるときの再取得の失敗では前の内容を出したままにする(UX 契約 messaging.md §3.6)。
 */
export function initialLoadError(query: { data: unknown; error: unknown }): unknown {
  return query.data === undefined ? query.error : null;
}

/** ドキュメント詳細。 */
export function useDocument(
  id: string | null,
  options: { refetchInterval?: number | false } = {},
) {
  return useQuery({
    queryKey: queryKeys.document(id ?? ""),
    queryFn: () => api.getDocument(id as string),
    enabled: id != null,
    // 削除済み・存在しない文書(404)は再試行しても変わらない。既定の 3 回の再試行で
    // 数秒 Skeleton のままにせず、すぐに見つからない旨を出す(#281)。
    retry: retryUnlessNotFound,
    refetchInterval: options.refetchInterval,
  });
}

/** 文書の chunk_set(variant)一覧。展開時のみ lazy 取得する。 */
export function useDocumentChunkSets(id: string | null, enabled = true) {
  return useQuery({
    queryKey: queryKeys.documentChunkSets(id ?? ""),
    queryFn: () => api.listDocumentChunkSets(id as string),
    enabled: id != null && enabled,
    retry: retryUnlessNotFound,
  });
}

/** 文書取込 segment/checkpoint 状態。 */
export function useDocumentIngestionSegments(id: string | null) {
  return useQuery({
    queryKey: queryKeys.documentIngestionSegments(id ?? ""),
    queryFn: () => api.listDocumentIngestionSegments(id as string),
    enabled: id != null,
    retry: retryUnlessNotFound,
  });
}

/** 文書単位の取込 job 履歴。workspace 側の一括ポーリングで更新する。 */
export function useDocumentIngestionJobs(id: string | null) {
  return useQuery({
    queryKey: queryKeys.documentIngestionJobs(id ?? ""),
    queryFn: () => api.listDocumentIngestionJobs(id as string),
    enabled: id != null,
    retry: retryUnlessNotFound,
  });
}

/** 失敗した segment checkpoint のみを再試行する取込 job を投入する。 */
export function useRetryFailedDocumentIngestionSegments() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, recipeId }: { id: string; recipeId: string | null }) =>
      api.retryFailedDocumentIngestionSegments(id, recipeId),
    onSuccess: (job, variables) => {
      qc.invalidateQueries({ queryKey: queryKeys.document(variables.id) });
      qc.invalidateQueries({
        queryKey: queryKeys.documentRecipes(variables.id),
      });
      qc.invalidateQueries({
        queryKey: queryKeys.documentIngestionJobs(variables.id),
      });
      qc.invalidateQueries({
        queryKey: queryKeys.documentIngestionSegments(variables.id),
      });
      qc.invalidateQueries({ queryKey: ["documents", "ingestion-jobs"] });
      qc.invalidateQueries({
        queryKey: ["documents", "ingestion-jobs", job.id],
      });
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

/** 文書の1〜3件の独立処理レシピ。 */
/** PDF のページ画像プレビュー用のページ一覧。描けないファイル（422）は再試行せず iframe に戻す。 */
export function useDocumentPreviewPages(
  id: string | null,
  options: { recipeId?: string | null; variant?: "original" | "prepared"; enabled?: boolean } = {},
) {
  const variant = options.variant ?? "original";
  return useQuery({
    queryKey: queryKeys.documentPreviewPages(id ?? "", options.recipeId ?? null, variant),
    queryFn: () =>
      api.getDocumentPreviewPages(id as string, { recipeId: options.recipeId, variant }),
    enabled: id != null && (options.enabled ?? true),
    retry: false,
    staleTime: 5 * 60_000,
  });
}

export function useDocumentRecipes(id: string | null) {
  return useQuery({
    queryKey: queryKeys.documentRecipes(id ?? ""),
    queryFn: () => api.listDocumentRecipes(id as string),
    enabled: id != null,
    retry: retryUnlessNotFound,
    refetchInterval: (query) => {
      const recipes = query.state.data;
      return recipes?.some((recipe) =>
        recipe.steps.some(
          (step) => step.status === "QUEUED" || step.status === "RUNNING",
        ),
      )
        ? 1_500
        : false;
    },
  });
}

export function useDocumentRecipeChunks(
  id: string | null,
  recipeId: string | null,
) {
  return useQuery({
    queryKey: queryKeys.documentRecipeChunks(id ?? "", recipeId ?? ""),
    queryFn: () =>
      api.listDocumentRecipeChunks(id as string, recipeId as string),
    enabled: id != null && recipeId != null,
    retry: retryUnlessNotFound,
  });
}

export function usePreviewDocumentRecipeChunks() {
  return useMutation({
    mutationFn: ({
      id,
      recipeId,
      payload,
    }: {
      id: string;
      recipeId: string;
      payload: DocumentChunkPreviewRequest;
    }) => api.previewDocumentRecipeChunks(id, recipeId, payload),
  });
}

export function useDocumentRecipeExtractionExport(
  id: string | null,
  recipeId: string | null,
  format: DocumentExtractionExportFormat,
) {
  return useQuery({
    queryKey: queryKeys.documentRecipeExtractionExport(
      id ?? "",
      recipeId ?? "",
      format,
    ),
    queryFn: () =>
      api.exportDocumentRecipeExtraction(
        id as string,
        recipeId as string,
        format,
      ),
    enabled: id != null && recipeId != null,
    retry: retryUnlessNotFound,
  });
}

function invalidateDocumentRecipeQueries(qc: QueryClient, documentId: string) {
  qc.invalidateQueries({ queryKey: queryKeys.documentRecipes(documentId) });
  qc.invalidateQueries({ queryKey: ["documents", documentId, "recipes"] });
  qc.invalidateQueries({
    queryKey: queryKeys.documentIngestionJobs(documentId),
  });
  qc.invalidateQueries({
    queryKey: queryKeys.documentIngestionSegments(documentId),
  });
  qc.invalidateQueries({ queryKey: queryKeys.document(documentId) });
}

export function useCreateDocumentRecipe() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, copyFrom }: { id: string; copyFrom: string | null }) =>
      api.createDocumentRecipe(id, copyFrom),
    onSuccess: (_recipe, variables) =>
      invalidateDocumentRecipeQueries(qc, variables.id),
  });
}

export function useDeleteDocumentRecipe() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, recipeId }: { id: string; recipeId: string }) =>
      api.deleteDocumentRecipe(id, recipeId),
    onSuccess: (_result, variables) =>
      invalidateDocumentRecipeQueries(qc, variables.id),
  });
}

export function useUpdateDocumentRecipe() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      recipeId,
      config,
    }: {
      id: string;
      recipeId: string;
      config: DocumentProcessingConfig;
    }) => api.updateDocumentRecipe(id, recipeId, config),
    onSuccess: (recipe, variables) => {
      qc.setQueryData(
        queryKeys.documentRecipes(variables.id),
        (current: Array<typeof recipe> | undefined) =>
          current?.map((item) =>
            item.recipe_id === recipe.recipe_id ? recipe : item,
          ),
      );
    },
  });
}

export function useEnqueueDocumentRecipeJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      recipeId,
      phase,
    }: {
      id: string;
      recipeId: string;
      phase: IngestionJobPhase;
    }) => api.enqueueDocumentRecipeJob(id, recipeId, phase),
    onSuccess: (_job, variables) =>
      invalidateDocumentRecipeQueries(qc, variables.id),
  });
}

export function useApproveDocumentRecipe() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      recipeId,
      payload,
    }: {
      id: string;
      recipeId: string;
      payload?: DocumentApproveRequest;
    }) => api.approveDocumentRecipe(id, recipeId, payload),
    onSuccess: (_job, variables) =>
      invalidateDocumentRecipeQueries(qc, variables.id),
  });
}

export function useSaveDocumentRecipeReviewEdits() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      recipeId,
      payload,
    }: {
      id: string;
      recipeId: string;
      payload: DocumentReviewEditsRequest;
    }) => api.saveDocumentRecipeReviewEdits(id, recipeId, payload),
    onSuccess: (_recipe, variables) =>
      invalidateDocumentRecipeQueries(qc, variables.id),
  });
}

/** 分類の入力の候補（保存済みの文書の分類の値。#547）。 */
export function useDocumentClassificationOptions() {
  return useQuery({
    queryKey: queryKeys.documentClassificationOptions,
    queryFn: () => api.getDocumentClassificationOptions(),
    staleTime: 60_000,
  });
}

/** 文書の分類と有効期間を保存する。 */
export function useSaveDocumentClassification() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: DocumentClassification }) =>
      api.saveDocumentClassification(id, payload),
    onSuccess: (detail) => {
      qc.setQueryData(queryKeys.document(detail.id), detail);
      // 新しく入力した値を、次の入力の候補に出す。
      qc.invalidateQueries({ queryKey: queryKeys.documentClassificationOptions });
    },
  });
}

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
      qc.invalidateQueries({
        queryKey: queryKeys.documentKnowledgeBases(variables.id),
      });
      qc.invalidateQueries({ queryKey: queryKeys.document(variables.id) });
      qc.invalidateQueries({
        queryKey: queryKeys.documentChunkSets(variables.id),
      });
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: ["knowledge-bases"] });
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
    },
  });
}

/** ファイルアップロード。成功時に一覧を無効化。 */
export function useUploadDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      file,
      knowledgeBaseIds = [],
      onProgress,
    }: {
      file: File;
      knowledgeBaseIds?: string[];
      /** 送信の進み具合（送信済み / 合計のバイト数。#306）。 */
      onProgress?: (progress: UploadProgress) => void;
    }) =>
      api.uploadDocument(file, knowledgeBaseIds, (transfer) =>
        onProgress?.(uploadProgressOf(0, file.size, transfer, file.size)),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: ["knowledge-bases"] });
    },
  });
}

/**
 * 複数ファイルアップロード。
 *
 * 1 リクエストの合計が 1 ファイルの上限以内になるよう分けて送り（前段 nginx の body 上限で一括全体が
 * 413 にならないように）、上限を超えるファイルは送らずに失敗として返す（#280）。途中のまとまりが
 * 失敗しても、それまでに保存できたファイルの結果は残す。認証切れ・権限なしは中断して投げ直す。
 * 送信の進み具合は、送るファイル全体のバイト数に対する送信済みのバイト数で通知する（#306）。
 */
export function useBatchUploadDocuments() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      files,
      knowledgeBaseIds = [],
      maxUploadBytes = DEFAULT_MAX_UPLOAD_BYTES,
      onProgress,
    }: {
      files: File[];
      knowledgeBaseIds?: string[];
      /** 1 ファイルの上限（upload-storage 設定の `max_upload_bytes`）。 */
      maxUploadBytes?: number;
      /** 送信の進み具合（送信済み / 合計のバイト数。#306）。 */
      onProgress?: (progress: UploadProgress) => void;
    }): Promise<BatchUploadResult> => {
      const { groups, oversized } = planUploadRequests(files, maxUploadBytes);
      const results: BatchUploadResult[] = [];
      const failed: BatchUploadFailedItem[] = oversized.map((file) =>
        failedUploadItem(
          file,
          413,
          t("upload.error.fileTooLarge", { size: formatByteSize(maxUploadBytes) }),
        ),
      );
      const totalBytes = totalUploadBytes(groups.flat());
      let doneBytes = 0;
      for (const group of groups) {
        const groupBytes = totalUploadBytes(group);
        try {
          results.push(
            await api.batchUploadDocuments(group, knowledgeBaseIds, (transfer) =>
              onProgress?.(uploadProgressOf(doneBytes, groupBytes, transfer, totalBytes)),
            ),
          );
        } catch (error) {
          if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
            throw error;
          }
          const status = error instanceof ApiError ? error.status : 0;
          const message = uploadErrorMessage(error);
          failed.push(...group.map((file) => failedUploadItem(file, status, message)));
        } finally {
          // 失敗したまとまりも「送信を終えた」量として数え、進み具合を後戻りさせない。
          doneBytes += groupBytes;
          onProgress?.({ sentBytes: doneBytes, totalBytes });
        }
      }
      return mergeBatchUploadResults(results, failed);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: ["knowledge-bases"] });
    },
  });
}

export function uploadErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    // 前段の proxy が返す 413 は ApiResponse の本文を持たないため、既定の「APIエラー (413)」を言い換える。
    if (error.status === 413 && error.isFallbackMessage) {
      return t("upload.error.requestTooLarge");
    }
    return error.message;
  }
  return t("upload.error.failed");
}

/** 取込 job 一覧。 */
export function useIngestionJobs(
  params: {
    status?: IngestionJobStatus;
    limit?: number;
    offset?: number;
  } = {},
) {
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
    refetchInterval: (query) =>
      ingestionJobIsActive(query.state.data?.status) ? 2000 : false,
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
    // 検索語・絞り込み・ページを変えている間は、前の一覧を出したまま取り直す（表を Skeleton に戻さない。#535）。
    // 応答は query key ごとに持つので、遅れて返った古い条件の応答が新しい条件の一覧を上書きしない。
    placeholderData: keepPreviousData,
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

/** ナレッジベースの項目抽出の定義（#548）。 */
export function useKnowledgeBaseExtractionFields(id: string) {
  return useQuery<KnowledgeBaseExtractionFieldsData>({
    queryKey: queryKeys.knowledgeBaseExtractionFields(id),
    queryFn: () => api.getKnowledgeBaseExtractionFields(id),
    retry: false,
  });
}

/** ナレッジベースの項目抽出の定義を保存する（#548）。 */
export function useUpdateKnowledgeBaseExtractionFields(id: string) {
  const onSuccess = useKnowledgeBaseExtractionFieldsSaved(id);
  return useMutation({
    mutationFn: (fields: ExtractionFieldDefinition[]) =>
      api.updateKnowledgeBaseExtractionFields(id, { fields }),
    onSuccess,
  });
}

/** ナレッジベースの項目抽出の定義を消し、全体の既定に戻す（#548）。 */
export function useResetKnowledgeBaseExtractionFields(id: string) {
  const onSuccess = useKnowledgeBaseExtractionFieldsSaved(id);
  return useMutation({
    mutationFn: () => api.updateKnowledgeBaseExtractionFields(id, { fields: null }),
    onSuccess,
  });
}

function useKnowledgeBaseExtractionFieldsSaved(id: string) {
  const qc = useQueryClient();
  return (data: KnowledgeBaseExtractionFieldsData) => {
    qc.setQueryData(queryKeys.knowledgeBaseExtractionFields(id), data);
    void qc.invalidateQueries({ queryKey: ["search", "extraction-fields"] });
  };
}

/** 検索の絞り込みに使える項目（選んだ業務ビューの KB の定義の和集合。#549）。 */
export function useSearchExtractionFields(businessViewIds: string[], enabled = true) {
  return useQuery<ExtractionFieldsSettingsData>({
    queryKey: queryKeys.searchExtractionFields(businessViewIds),
    queryFn: () => api.getSearchExtractionFields(businessViewIds),
    enabled: enabled && businessViewIds.length > 0,
    retry: false,
  });
}

export function useKnowledgeBaseGraph(id: string | null) {
  return useQuery({
    queryKey: queryKeys.knowledgeBaseGraph(id ?? ""),
    queryFn: () => api.getKnowledgeBaseGraph(id as string),
    enabled: id != null,
  });
}

/** ナレッジベース作成。 */
export function useCreateKnowledgeBase() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: KnowledgeBaseCreateRequest) =>
      api.createKnowledgeBase(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["knowledge-bases"] });
    },
  });
}

/** ナレッジベース更新。 */
export function useUpdateKnowledgeBase() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      payload,
    }: {
      id: string;
      payload: KnowledgeBaseUpdateRequest;
    }) => api.updateKnowledgeBase(id, payload),
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

/** 選択肢として 1 回に取得する KB の件数。これを超える分は「さらに表示」で次のページを取る。 */
export const KNOWLEDGE_BASE_SEARCH_PAGE_SIZE = 50;

/**
 * KB を選ぶ UI 用のサーバー側検索（名前・説明の部分一致 + ページング。#302）。
 * 先頭のページだけで打ち切らず、`fetchNextPage` で続きを取れる。検索語を変えている間は
 * 直前の結果を出したままにする（一覧が空に戻ってちらつかないように）。
 */
export function useKnowledgeBaseSearch(
  params: { status?: KnowledgeBaseStatus; q?: string },
  { enabled = true }: { enabled?: boolean } = {},
) {
  return useInfiniteQuery({
    queryKey: queryKeys.knowledgeBaseSearch(params),
    queryFn: ({ pageParam }) =>
      api.listKnowledgeBases({
        ...params,
        limit: KNOWLEDGE_BASE_SEARCH_PAGE_SIZE,
        offset: pageParam,
      }),
    initialPageParam: 0,
    getNextPageParam: (lastPage) =>
      lastPage.has_next ? lastPage.offset + lastPage.items.length : undefined,
    placeholderData: keepPreviousData,
    enabled,
  });
}

/**
 * 画面側で絞り込む KB の件数の上限（= 一覧 API の `limit` の最大値。1 回の取得で全件が揃う）。
 * これを超えるときはサーバー側の検索（`GET /api/knowledge-bases?q=`）に切り替え、全件を読まない（#578）。
 */
export const KNOWLEDGE_BASE_LOCAL_FILTER_LIMIT = 200;

/**
 * KB の選択肢（検索できる選択部品用。#578）。
 *
 * - まず先頭の 200 件（`KNOWLEDGE_BASE_LOCAL_FILTER_LIMIT`）を 1 回で取る。全件がそこに収まれば、
 *   選択肢は手元の全件で、検索は画面側で絞り込む（`remote: false`。1 文字ごとに問い合わせない）。
 * - 収まらない（201 件以上）ときは、検索語 `q` でサーバー側を検索し、50 件ずつ続きを読む（`remote: true`）。
 *   全件をページ送りで読み切らない（数百件でも最初の表示と検索が遅くならない）。
 */
export function useKnowledgeBaseChoices(params: { status?: KnowledgeBaseStatus; q: string }) {
  const head = useQuery({
    queryKey: queryKeys.knowledgeBases({
      status: params.status,
      limit: KNOWLEDGE_BASE_LOCAL_FILTER_LIMIT,
      offset: 0,
    }),
    queryFn: () =>
      api.listKnowledgeBases({
        status: params.status,
        limit: KNOWLEDGE_BASE_LOCAL_FILTER_LIMIT,
        offset: 0,
      }),
  });
  const remote = Boolean(head.data?.has_next);
  const search = useKnowledgeBaseSearch(
    { status: params.status, q: params.q || undefined },
    { enabled: remote },
  );
  const items: KnowledgeBaseSummary[] = remote
    ? (search.data?.pages.flatMap((page) => page.items) ?? [])
    : (head.data?.items ?? []);
  return {
    /** サーバー側で検索している（全件を持たない）。 */
    remote,
    items,
    /** 条件（status）に合う KB の総数（検索語によらない）。 */
    total: head.data?.total ?? 0,
    /** 検索語に一致する件数（サーバー側の検索のとき）。 */
    matchedTotal: remote ? (search.data?.pages[0]?.total ?? 0) : items.length,
    isPending: head.isPending || (remote && search.isPending),
    isError: head.isError || (remote && search.isError),
    error: head.error ?? search.error,
    isFetching: head.isFetching || search.isFetching,
    /** 検索語を変えて取り直している（前の結果を出したまま）。 */
    searching: remote && search.isPlaceholderData && search.isFetching,
    hasMore: remote && Boolean(search.hasNextPage),
    loadingMore: remote && search.isFetchingNextPage,
    loadMore: () => void search.fetchNextPage(),
    refetch: () => {
      void head.refetch();
      if (remote) void search.refetch();
    },
  };
}

/** 一覧 API の `ids` の上限（backend の `MAX_KNOWLEDGE_BASE_ID_FILTER`）。 */
const KNOWLEDGE_BASE_ID_LOOKUP_LIMIT = 200;

/**
 * 選択済みの KB を ID で引く（アーカイブ済みを含む。#302）。検索結果のページに無い選択済みの
 * KB もチップに名前と状態を出すために使う。返らない ID は、存在しないか利用者の範囲外。
 */
export function useKnowledgeBasesByIds(ids: string[]) {
  const sorted = [...new Set(ids)].sort().slice(0, KNOWLEDGE_BASE_ID_LOOKUP_LIMIT);
  return useQuery({
    queryKey: queryKeys.knowledgeBases({ ids: sorted, limit: KNOWLEDGE_BASE_ID_LOOKUP_LIMIT }),
    queryFn: () => api.listKnowledgeBases({ ids: sorted, limit: KNOWLEDGE_BASE_ID_LOOKUP_LIMIT }),
    enabled: sorted.length > 0,
    placeholderData: keepPreviousData,
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
    // 検索語・絞り込み・ページを変えている間は、前の一覧を出したまま取り直す（#535。useKnowledgeBases と同じ）。
    placeholderData: keepPreviousData,
  });
}

/** 業務ビュー詳細(config・参照 KB を含む)。 */
export function useBusinessView(id: string | null) {
  return useQuery({
    queryKey: queryKeys.businessView(id ?? ""),
    queryFn: () => api.getBusinessView(id as string),
    enabled: id != null,
    // URL の `?id=` の対象が無い（404）ときは再試行せず、すぐ「見つかりません」を出す。
    retry: (failureCount, error) =>
      !(error instanceof ApiError && error.status === 404) && failureCount < 3,
  });
}

/** 業務ビューのドメインキーワード。 */
export function useDomainKeywords(businessViewId: string) {
  return useQuery({
    queryKey: ["business-views", businessViewId, "domain-keywords"],
    queryFn: () => api.getDomainKeywords(businessViewId),
  });
}

/** ドメインキーワードの保存(全置換)。 */
export function useSaveDomainKeywords(businessViewId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (keywords: string[]) =>
      api.saveDomainKeywords(businessViewId, keywords),
    onSuccess: (data) => {
      qc.setQueryData(
        ["business-views", businessViewId, "domain-keywords"],
        data,
      );
    },
  });
}

/** 参照 KB からドメインキーワード候補を生成する(保存しない)。 */
export function useSuggestDomainKeywords(businessViewId: string) {
  return useMutation({
    mutationFn: () => api.suggestDomainKeywords(businessViewId),
  });
}

/** 業務ビューの承認済み FAQ(類似問)。 */
export function useApprovedFaq(businessViewId: string) {
  return useQuery({
    queryKey: ["business-views", businessViewId, "approved-faq"],
    queryFn: () => api.getApprovedFaq(businessViewId),
  });
}

/** FAQ の追加・削除・取込。成功時は一覧 cache を結果で置き換える。 */
export function useApprovedFaqMutation<TArgs>(
  businessViewId: string,
  mutationFn: (args: TArgs) => Promise<ApprovedFaqMutationData>,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: (data) => {
      qc.setQueryData(["business-views", businessViewId, "approved-faq"], data);
    },
  });
}

/** 業務ビューの用語・ルール。 */
export function useRuntimeKnowledge(businessViewId: string) {
  return useQuery({
    queryKey: ["business-views", businessViewId, "runtime-knowledge"],
    queryFn: () => api.getRuntimeKnowledge(businessViewId),
  });
}

/** 用語・ルールの 1 行追加・更新・削除。 */
export function useEditRuntimeKnowledge(businessViewId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: RuntimeKnowledgeEditRequest) =>
      api.editRuntimeKnowledge(businessViewId, body),
    onSuccess: (data) => {
      qc.setQueryData(
        ["business-views", businessViewId, "runtime-knowledge"],
        data,
      );
    },
  });
}

/**
 * 指定した trace_id のうち、保存済みの DocRAG 回答があるもの（チャットの会話の回答用。#304）。
 * 開いている会話の回答だけを引き当てる。
 */
export function useSavedDocragTraceIds(businessViewId: string | null, traceIds: string[]) {
  const ids = traceIds.slice(-ANSWER_TRACE_ID_FILTER_MAX);
  return useQuery({
    queryKey: ["docrag-answers", businessViewId, "trace-ids", ids],
    queryFn: async () => {
      const page = await api.listDocragAnswers({
        businessViewId: businessViewId as string,
        limit: ids.length,
        traceIds: ids,
      });
      return new Set(page.items.map((answer) => answer.trace_id));
    },
    enabled: Boolean(businessViewId) && ids.length > 0,
  });
}

/** 保存済み DocRAG 回答 1 件。traceId が null の間は取得しない。 */
export function useDocragAnswer(traceId: string | null) {
  return useQuery({
    queryKey: ["docrag-answer", traceId],
    queryFn: () => api.getDocragAnswer(traceId as string),
    enabled: Boolean(traceId),
  });
}

/** 保存済み DocRAG 回答を標準回答で評価する(LLM を複数回呼ぶ)。詳細のキャッシュを更新する。 */
export function useEvaluateDocragAnswer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ traceId, standardAnswer }: { traceId: string; standardAnswer: string }) =>
      api.evaluateDocragAnswer(traceId, standardAnswer),
    onSuccess: (detail) => {
      qc.setQueryData(["docrag-answer", detail.trace_id], detail);
    },
  });
}

/** 質問履歴の設定。 */
export function useQueryHistorySettings() {
  return useQuery({ queryKey: ["settings", "query-history"], queryFn: api.getQueryHistorySettings });
}

export function useUpdateQueryHistorySettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: QueryHistorySettingsData) => api.updateQueryHistorySettings(body),
    onSuccess: (data) => {
      qc.setQueryData(["settings", "query-history"], data);
      qc.invalidateQueries({ queryKey: ["query-suggestions"] });
    },
  });
}

/** 業務ビューでよく聞かれる質問(質問履歴が有効なときだけ候補が返る)。 */
export function useQuerySuggestions(
  businessViewId: string | null,
  query: string,
  filters: Record<string, string>
) {
  return useQuery({
    queryKey: ["query-suggestions", businessViewId, query, filters],
    queryFn: () => api.getQuerySuggestions(businessViewId as string, query, filters),
    enabled: Boolean(businessViewId),
    staleTime: 30_000,
    placeholderData: (previous) => previous,
  });
}

/** 編集できる DocRAG プロンプトと、回答フローの各段の読み取り専用プロンプト。 */
export function useDocragPrompts() {
  return useQuery({ queryKey: ["settings", "docrag-prompts"], queryFn: api.getDocragPrompts });
}

/** DocRAG プロンプトの保存(content あり)と既定値への復帰(content なし)。 */
export function useSaveDocragPrompt() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ key, content }: { key: DocragPromptKey; content: string | null }) =>
      content === null ? api.resetDocragPrompt(key) : api.saveDocragPrompt(key, content),
    onSuccess: (data) => qc.setQueryData(["settings", "docrag-prompts"], data),
  });
}

/** 回答 feedback を業務ビューの Approved FAQ へ登録する(同じ質問は置き換える)。 */
export function usePromoteFeedbackToApprovedFaq() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (feedbackId: string) => api.promoteFeedbackToApprovedFaq(feedbackId),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["business-views", result.business_view_id, "approved-faq"] });
    },
  });
}

/** 回答 feedback から品質評価のケースを作る。 */
export function useFeedbackEvaluationCase() {
  return useMutation({
    mutationFn: (feedbackId: string) => api.getFeedbackEvaluationCase(feedbackId),
  });
}

/** 保存済み DocRAG 回答の削除。一覧・詳細のキャッシュを捨てる。 */
export function useDeleteDocragAnswer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (traceId: string) => api.deleteDocragAnswer(traceId),
    onSuccess: (_data, traceId) => {
      qc.removeQueries({ queryKey: ["docrag-answer", traceId] });
      qc.invalidateQueries({ queryKey: ["docrag-answers"] });
    },
  });
}

/** DocRAG 回答記録の保持日数。 */
export function useAnswerRecordSettings() {
  return useQuery({
    queryKey: ["settings", "answer-records"],
    queryFn: api.getAnswerRecordSettings,
  });
}

/** DocRAG 回答記録の保持日数を保存する(期限切れは backend が削除する)。 */
export function useUpdateAnswerRecordSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (retentionDays: number) =>
      api.updateAnswerRecordSettings({ retention_days: retentionDays }),
    onSuccess: (data) => {
      qc.setQueryData(["settings", "answer-records"], data);
      qc.invalidateQueries({ queryKey: ["docrag-answers"] });
    },
  });
}

/** 業務ビュー作成。 */
export function useCreateBusinessView() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: BusinessViewCreateRequest) =>
      api.createBusinessView(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["business-views"] });
    },
  });
}

/** 業務ビュー更新。 */
export function useUpdateBusinessView() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      payload,
    }: {
      id: string;
      payload: BusinessViewUpdateRequest;
    }) => api.updateBusinessView(id, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["business-views"] });
    },
  });
}

/** チャット会話一覧(業務ビュー scope)。 */
export function useConversations(params: {
  business_view_id?: string;
  limit?: number;
  offset?: number;
}) {
  return useQuery({
    queryKey: queryKeys.conversations(params),
    queryFn: () => api.listConversations(params),
    enabled: params.business_view_id != null,
    retry: false,
    // ページを送る間は前のページを出したままにする（同じ業務ビューの間だけ。#403）。
    placeholderData: (previous, previousQuery) =>
      (previousQuery?.queryKey[1] as { business_view_id?: string } | undefined)?.business_view_id ===
      params.business_view_id
        ? previous
        : undefined,
  });
}

/** チャット会話詳細(メッセージ列を含む)。 */
export function useConversation(id: string | null) {
  return useQuery({
    queryKey: queryKeys.conversation(id ?? ""),
    queryFn: () => api.getConversation(id as string),
    enabled: id != null,
  });
}

/** チャット会話作成。 */
export function useCreateConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: ConversationCreateBody) =>
      api.createConversation(payload),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
}

/** trace ごとの自分の最新フィードバック。 */
export function useCurrentFeedback(traceId: string | null) {
  return useQuery({
    queryKey: queryKeys.currentFeedback(traceId ?? ""),
    queryFn: () => api.getCurrentFeedback(traceId as string),
    enabled: Boolean(traceId),
  });
}

/** 回答/引用フィードバックを追記する。 */
export function useSubmitFeedback() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: FeedbackRequestBody) => api.submitFeedback(payload),
    onSuccess: (saved) => {
      qc.setQueryData(
        queryKeys.currentFeedback(saved.trace_id),
        (current: unknown) => {
          const items = Array.isArray(current) ? current : [];
          const remaining = items.filter((item) => {
            if (!item || typeof item !== "object") return false;
            const candidate = item as Partial<typeof saved>;
            return !(
              candidate.target_type === saved.target_type &&
              (candidate.document_id ?? null) === (saved.document_id ?? null) &&
              (candidate.chunk_id ?? null) === (saved.chunk_id ?? null)
            );
          });
          return [
            ...remaining,
            { ...saved, created_at: new Date().toISOString() },
          ];
        },
      );
      qc.invalidateQueries({ queryKey: ["feedback"] });
    },
  });
}

/** 管理者向けフィードバック集計。 */
export function useFeedbackDashboard(params: FeedbackListParams) {
  return useQuery({
    queryKey: queryKeys.feedback(params),
    queryFn: () => api.listFeedback(params),
  });
}

/** 管理者向けフィードバック本文・実行情報を、drawer を開いた時だけ取得する。 */
export function useFeedbackDetail(id: string | null) {
  return useQuery({
    queryKey: queryKeys.feedbackDetail(id ?? ""),
    queryFn: () => api.getFeedbackDetail(id as string),
    enabled: id != null,
  });
}

/** チャット会話名更新。 */
export function useUpdateConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      payload,
    }: {
      id: string;
      payload: ConversationUpdateBody;
    }) => api.updateConversation(id, payload),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
}

/** チャット会話削除。削除した会話の詳細は再取得（404）させずに捨てる。 */
export function useDeleteConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteConversation(id),
    onSuccess: async (_data, id) => {
      qc.removeQueries({ queryKey: queryKeys.conversation(id), exact: true });
      await qc.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
}

/** 比較で選べる設定済み OCI モデル一覧。 */
export function useCompareModels(enabled = true) {
  return useQuery({
    queryKey: queryKeys.compareModels,
    queryFn: () => api.listCompareModels(),
    enabled,
    staleTime: 5 * 60 * 1000,
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
    mutationFn: ({
      knowledgeBaseId,
      documentId,
    }: {
      knowledgeBaseId: string;
      documentId: string;
    }) => api.removeDocumentFromKnowledgeBase(knowledgeBaseId, documentId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["knowledge-bases"] });
      qc.invalidateQueries({ queryKey: ["documents"] });
    },
  });
}

/** 文書を取込 job へ投入する。 */
export function useEnqueueDocumentIngestionJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      force,
      phase,
    }: {
      id: string;
      force?: boolean;
      phase?: IngestionJobPhase;
    }) => api.enqueueDocumentIngestionJob(id, force, phase),
    onSuccess: (job) => {
      if (
        (job.phase === "PREPROCESS" || job.phase === "EXTRACT") &&
        job.status === "QUEUED"
      ) {
        clearDocumentProcessingCache(qc, job.document_id, {
          clearPreprocessArtifact: job.phase === "PREPROCESS",
        });
      }
      invalidateDocumentProcessingQueries(qc, job.document_id);
    },
  });
}

/** 永続化済み QUEUED job を再実行する。 */
export function useDrainIngestionJobs() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ limit = 50 }: { limit?: number } = {}) =>
      api.drainIngestionJobs(limit),
    onSuccess: (jobs) => {
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: ["documents", "ingestion-jobs"] });
      for (const job of jobs) {
        qc.invalidateQueries({ queryKey: queryKeys.document(job.document_id) });
      }
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
      qc.invalidateQueries({
        queryKey: queryKeys.documentIngestionJobs(job.document_id),
      });
      qc.invalidateQueries({
        queryKey: queryKeys.documentIngestionSegments(job.document_id),
      });
      qc.invalidateQueries({ queryKey: ["documents", "ingestion-jobs"] });
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
    },
  });
}

/** 品質評価の job の query key（#390）。 */
export const evaluationJobQueryKey = (jobId: string) => ["evaluation", "jobs", jobId] as const;

/** RAG golden set 評価を job として投入する（#390）。 */
export function useSubmitRunEvaluationJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: EvaluationRunRequestBody) => api.submitRunEvaluationJob(payload),
    onSuccess: (job) => qc.setQueryData(evaluationJobQueryKey(job.job_id), job),
  });
}

/** RAG 設定比較を job として投入する（#390）。 */
export function useSubmitCompareEvaluationJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: EvaluationCompareRequestBody) => api.submitCompareEvaluationJob(payload),
    onSuccess: (job) => qc.setQueryData(evaluationJobQueryKey(job.job_id), job),
  });
}

/** 実行中の品質評価の job は状態を取得し続け、終わったら止める。 */
export function evaluationJobRefetchInterval(job: EvaluationJob | undefined): number | false {
  return job?.status === "RUNNING" ? EVALUATION_JOB_POLL_INTERVAL_MS : false;
}

/**
 * 品質評価の job の状態（進捗・結果）。画面を離れて戻っても、保存した job id でサーバーの状態を
 * 確かめる（workspace-state.md）。見つからない job（404）は再試行しない。
 */
export function useEvaluationJob(jobId: string | null) {
  return useQuery({
    queryKey: evaluationJobQueryKey(jobId ?? ""),
    queryFn: () => api.getEvaluationJob(jobId ?? ""),
    enabled: Boolean(jobId),
    retry: (failureCount, error) =>
      !(error instanceof ApiError && error.status === 404) && failureCount < 3,
    refetchInterval: (query) =>
      query.state.error ? false : evaluationJobRefetchInterval(query.state.data),
  });
}

/** 品質評価の job を取り消す。 */
export function useCancelEvaluationJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => api.cancelEvaluationJob(jobId),
    onSuccess: (job) => qc.setQueryData(evaluationJobQueryKey(job.job_id), job),
  });
}

/** モデル設定。 */
export function useModelSettings() {
  return useQuery({
    queryKey: queryKeys.modelSettings,
    queryFn: api.getModelSettings,
  });
}

/** HuggingFace モデルダウンロード設定。 */
export function useHuggingFaceSettings() {
  return useQuery({
    queryKey: queryKeys.huggingfaceSettings,
    queryFn: api.getHuggingFaceSettings,
  });
}

/** HuggingFace token / mirror endpoint のランタイム保存。 */
export function useUpdateHuggingFaceSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: HuggingFaceSettingsUpdate) =>
      api.updateHuggingFaceSettings(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.huggingfaceSettings });
    },
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
export function useParserAdapterSettings(enabled = true) {
  return useQuery<ParserAdapterSettingsData>({
    queryKey: queryKeys.parserAdapterSettings,
    queryFn: api.getParserAdapterSettings,
    enabled,
    retry: false,
  });
}

export function useExternalParserStatus(
  backend: ExternalParserBackendName,
  enabled = false,
) {
  return useQuery<ExternalParserConnectionStatusData>({
    queryKey: queryKeys.externalParserStatus(backend),
    queryFn: () => api.getExternalParserStatus(backend),
    enabled,
    retry: false,
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
    mutationFn: (payload: GraphSettingsUpdate) =>
      api.updateGraphSettings(payload),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.graphSettings, data);
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
    mutationFn: (payload: AgenticSettingsUpdate) =>
      api.updateAgenticSettings(payload),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.agenticSettings, data);
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
    mutationFn: (payload: EvaluationSettingsUpdate) =>
      api.updateEvaluationSettings(payload),
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
    mutationFn: (payload: VectorIndexSettingsUpdate) =>
      api.updateVectorIndexSettings(payload),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.vectorIndexSettings, data);
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
    mutationFn: (payload: GenerationSettingsUpdate) =>
      api.updateGenerationSettings(payload),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.generationSettings, data);
    },
  });
}

/** 回答プロンプト版の一覧と有効版(custom 回答スタイルが使用)。 */
export function usePromptVersions() {
  return useQuery<PromptVersionsData>({
    queryKey: queryKeys.promptVersions,
    queryFn: api.getPromptVersions,
    retry: false,
  });
}

/** 有効版変更は custom 回答スタイルの実体に影響するため generationSettings も無効化する。 */
function invalidatePromptDependents(qc: QueryClient, data: PromptVersionsData) {
  qc.setQueryData(queryKeys.promptVersions, data);
  qc.invalidateQueries({ queryKey: queryKeys.generationSettings });
}

/** 新しい回答プロンプト版を作成(activate=true で即時有効化)。 */
export function useCreatePromptVersion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: PromptVersionCreate) =>
      api.createPromptVersion(payload),
    onSuccess: (data) => invalidatePromptDependents(qc, data),
  });
}

/** 指定の回答プロンプト版を有効化(rollback = 旧版の再有効化)。 */
export function useActivatePromptVersion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (versionId: string) => api.activatePromptVersion(versionId),
    onSuccess: (data) => invalidatePromptDependents(qc, data),
  });
}

/** メタデータ/項目抽出のスキーマ定義。項目抽出トグルの警告表示などに使う。 */
export function useExtractionFieldsSettings(enabled = true) {
  return useQuery<ExtractionFieldsSettingsData>({
    queryKey: queryKeys.extractionFieldsSettings,
    queryFn: api.getExtractionFieldsSettings,
    enabled,
    retry: false,
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
    mutationFn: (payload: GuardrailSettingsUpdate) =>
      api.updateGuardrailSettings(payload),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.guardrailSettings, data);
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
    mutationFn: (payload: RetrievalSettingsUpdate) =>
      api.updateRetrievalSettings(payload),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.retrievalSettings, data);
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
    mutationFn: (payload: GroundingSettingsUpdate) =>
      api.updateGroundingSettings(payload),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.groundingSettings, data);
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
export function useServiceCatalog(
  options: { refetchInterval?: number | false } = {},
) {
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
  options: { refetchInterval?: number | false } = {},
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

/** サービスログ末尾。ユーザーがログを開いた時だけ取得する。 */
export function useServiceLogs(serviceId: string | null, lines = 200) {
  return useQuery<ServiceLogsData>({
    queryKey: queryKeys.serviceLogs(serviceId ?? "", lines),
    queryFn: () => api.getServiceLogs(serviceId ?? "", lines),
    enabled: Boolean(serviceId),
    retry: false,
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
    mutationFn: ({ serviceId, action }) =>
      api.controlService(serviceId, action),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.services });
    },
  });
}

/** 前処理(Preprocess)アダプター設定をランタイム保存。 */
export function useUpdatePreprocessSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: PreprocessSettingsUpdate) =>
      api.updatePreprocessSettings(payload),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.preprocessSettings, data);
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
    mutationFn: (payload: ChunkingSettingsUpdate) =>
      api.updateChunkingSettings(payload),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.chunkingSettings, data);
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
      void qc.resetQueries({ queryKey: queryKeys.externalParserStatuses });
      // 設定の概要の全体の既定（解析エンジン・解析後の処理）も変わる（#528）。
      void qc.invalidateQueries({ queryKey: queryKeys.pipelineSettings });
    },
  });
}

/** 設定の概要: 工程の自動進行と、レシピ 11 項目の全体の既定（#528）。 */
export function usePipelineSettings() {
  return useQuery<PipelineSettingsData>({
    queryKey: queryKeys.pipelineSettings,
    queryFn: api.getPipelineSettings,
    retry: false,
  });
}

/** 工程の自動進行を保存する（#528）。 */
export function useUpdatePipelineSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: PipelineSettingsUpdate) => api.updatePipelineSettings(payload),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.pipelineSettings, data);
    },
  });
}

/** 抽出項目の定義を保存する（文書解析の「解析後の処理」。#528）。 */
export function useUpdateExtractionFieldsSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (fields: ExtractionFieldDefinition[]) =>
      api.updateExtractionFieldsSettings({ fields }),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.extractionFieldsSettings, data);
      // 全体の既定は、定義を持たない KB と検索の項目の候補にも効く（#548）。
      void qc.invalidateQueries({
        predicate: (query) =>
          query.queryKey[0] === "knowledge-bases" && query.queryKey[2] === "extraction-fields",
      });
      void qc.invalidateQueries({ queryKey: ["search", "extraction-fields"] });
    },
  });
}
