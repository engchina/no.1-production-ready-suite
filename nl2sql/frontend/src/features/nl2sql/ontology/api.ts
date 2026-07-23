import type {
  GraphPatch,
  OntologyBuildJob,
  OntologyGraph,
  OntologyImprovementProposalRequest,
  OntologyProposal,
  OntologyProposalReviewData,
  OntologyProfileRecommendation,
  OntologyPublishJob,
  OntologyRevision,
  QuerySession,
  QuerySessionCreateRequest,
  QuerySessionExecuteRequest,
  QuerySessionGenerateSqlRequest,
  QuerySessionSqlConfirmationRequest,
} from "./types";
import { apiFetch } from "../../../lib/api.ts";

interface ApiEnvelope<T> {
  data?: T;
  error?: string;
  detail?: unknown;
}

interface RequestOptions {
  signal?: AbortSignal;
  idempotencyKey?: string;
  ifMatch?: string;
}

// HTTP status を保持するエラー(ポーリング側で 404 = job 消失を判別するため)
export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export class QuerySessionVersionConflictError extends Error {
  readonly status = 409;
  readonly currentVersion: number | null;
  readonly session: QuerySession | null;

  constructor(message: string, currentVersion: number | null, session: QuerySession | null) {
    super(message);
    this.name = "QuerySessionVersionConflictError";
    this.currentVersion = currentVersion;
    this.session = session;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizeQuerySession(value: unknown): QuerySession {
  if (!isRecord(value)) return value as QuerySession;
  if (!isRecord(value.session)) return value as unknown as QuerySession;
  const graphData = isRecord(value.ontology_graph) ? value.ontology_graph : null;
  const revision = graphData && isRecord(graphData.revision) ? graphData.revision : null;
  const ontologyGraph = graphData
    ? {
        revision_id: typeof revision?.id === "string" ? revision.id : undefined,
        nodes: Array.isArray(graphData.nodes) ? graphData.nodes : [],
        edges: Array.isArray(graphData.edges) ? graphData.edges : [],
      }
    : null;
  const profileView = isRecord(value.profile_ontology_view)
    ? { ...value.profile_ontology_view, graph: ontologyGraph }
    : null;
  return {
    ...(value.session as unknown as QuerySession),
    profile_ontology_view: profileView as QuerySession["profile_ontology_view"],
    ontology_graph: ontologyGraph as QuerySession["ontology_graph"],
    result: isRecord(value.result) ? (value.result as QuerySession["result"]) : null,
    performance_check: isRecord(value.performance_check)
      ? (value.performance_check as unknown as QuerySession["performance_check"])
      : null,
  };
}

function payloadMessage(payload: ApiEnvelope<unknown>, status: number): string {
  // 共通例外ハンドラは ApiResponse { error_messages: [...] } 形式で返す
  const errorMessages = (payload as { error_messages?: unknown }).error_messages;
  if (Array.isArray(errorMessages) && errorMessages.length > 0) {
    return errorMessages.map(String).join(" ");
  }
  if (payload.error) return payload.error;
  if (typeof payload.detail === "string") return payload.detail;
  if (isRecord(payload.detail)) {
    const message = payload.detail.message_ja ?? payload.detail.message ?? payload.detail.error;
    if (typeof message === "string") return message;
  }
  return `クエリセッション API の呼び出しに失敗しました（HTTP ${status}）。`;
}

function conflictDetails(payload: ApiEnvelope<unknown>): {
  currentVersion: number | null;
  session: QuerySession | null;
} {
  const detail = isRecord(payload.detail) ? payload.detail : {};
  const data = isRecord(payload.data) ? payload.data : {};
  const rawVersion = detail.current_version ?? data.current_version;
  const rawSession = detail.session ?? data.session;
  return {
    currentVersion: typeof rawVersion === "number" ? rawVersion : null,
    session: isRecord(rawSession) ? (rawSession as unknown as QuerySession) : null,
  };
}

async function request<T>(
  path: string,
  method: "GET" | "POST" | "PATCH",
  body?: unknown,
  options: RequestOptions = {}
): Promise<T> {
  const idempotencyKey =
    method === "POST"
      ? options.idempotencyKey ?? createIdempotencyKey(path, body)
      : undefined;
  const response = await apiFetch(path, {
    method,
    signal: options.signal,
    headers: {
      Accept: "application/json",
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
      ...(options.ifMatch ? { "If-Match": `"${options.ifMatch}"` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let payload: ApiEnvelope<T>;
  try {
    payload = (await response.json()) as ApiEnvelope<T>;
  } catch {
    payload = {};
  }
  if (!response.ok) {
    const message = payloadMessage(payload, response.status);
    if (response.status === 409) {
      const { currentVersion, session } = conflictDetails(payload);
      throw new QuerySessionVersionConflictError(message, currentVersion, session);
    }
    throw new ApiError(message, response.status);
  }
  if (payload.data !== undefined) return payload.data;
  return payload as T;
}

function createIdempotencyKey(path: string, body: unknown): string {
  const random =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const bodyHint = isRecord(body)
    ? Object.keys(body)
        .sort()
        .join(".")
    : "empty";
  return `nl2sql:${path}:${bodyHint}:${random}`;
}

export function querySessionPath(sessionId?: string, action?: string): string {
  const root = "/api/nl2sql/query-sessions";
  if (!sessionId) return root;
  const sessionPath = `${root}/${encodeURIComponent(sessionId)}`;
  return action ? `${sessionPath}/${action}` : sessionPath;
}

export function createQuerySession(
  payload: QuerySessionCreateRequest,
  options?: RequestOptions
): Promise<QuerySession> {
  return request<unknown>(querySessionPath(), "POST", payload, options).then(normalizeQuerySession);
}

export function getQuerySession(sessionId: string, options?: RequestOptions): Promise<QuerySession> {
  return request<unknown>(querySessionPath(sessionId), "GET", undefined, options).then(normalizeQuerySession);
}

export function patchQuerySessionIntent(
  sessionId: string,
  patch: GraphPatch,
  options?: RequestOptions
): Promise<QuerySession> {
  return request<unknown>(querySessionPath(sessionId, "intent"), "PATCH", patch, options).then(normalizeQuerySession);
}

export function generateQuerySessionSql(
  sessionId: string,
  payload: QuerySessionGenerateSqlRequest,
  options?: RequestOptions
): Promise<QuerySession> {
  return request<unknown>(querySessionPath(sessionId, "generate-sql"), "POST", payload, options).then(normalizeQuerySession);
}

export function confirmQuerySessionSql(
  sessionId: string,
  payload: QuerySessionSqlConfirmationRequest,
  options?: RequestOptions
): Promise<QuerySession> {
  return request<unknown>(querySessionPath(sessionId, "confirm-sql"), "POST", payload, options).then(normalizeQuerySession);
}

export function executeQuerySession(
  sessionId: string,
  payload: QuerySessionExecuteRequest,
  options?: RequestOptions
): Promise<QuerySession> {
  return request<unknown>(querySessionPath(sessionId, "execute"), "POST", payload, options).then(normalizeQuerySession);
}

export function createOntologyImprovementProposal(
  sessionId: string,
  payload: OntologyImprovementProposalRequest,
  options?: RequestOptions
): Promise<OntologyProposal> {
  return request(querySessionPath(sessionId, "improvement-proposal"), "POST", payload, options);
}

// --- AI オントロジー構築 ---

// POST はバックエンドで Q/A ファイルを同期パースするため、無応答時の固まり防止に timeout を設ける
const ONTOLOGY_BUILD_START_TIMEOUT_MS = 30_000;

export interface OntologyBuildStartInput {
  businessText: string;
  qaFile?: File | null;
  sourceFiles?: File[];
  runSchemaNaming: boolean;
  runQaExtraction: boolean;
  runTextExtraction: boolean;
}

export async function startOntologyBuild(
  profileId: string,
  input: OntologyBuildStartInput
): Promise<OntologyBuildJob> {
  const form = new FormData();
  form.set("business_text", input.businessText);
  form.set("run_schema_naming", String(input.runSchemaNaming));
  form.set("run_qa_extraction", String(input.runQaExtraction));
  form.set("run_text_extraction", String(input.runTextExtraction));
  if (input.qaFile) form.set("qa_file", input.qaFile, input.qaFile.name);
  for (const sourceFile of input.sourceFiles ?? []) {
    form.append("source_files", sourceFile, sourceFile.name);
  }
  const idempotencyKey = createIdempotencyKey(
    `/api/nl2sql/profiles/${profileId}/ontology-build`,
    { filenames: (input.sourceFiles ?? []).map((file) => file.name) }
  );
  const response = await apiFetch(
    `/api/nl2sql/profiles/${encodeURIComponent(profileId)}/ontology-build`,
    {
      method: "POST",
      headers: { Accept: "application/json", "Idempotency-Key": idempotencyKey },
      body: form,
      signal: AbortSignal.timeout(ONTOLOGY_BUILD_START_TIMEOUT_MS),
    }
  );
  let payload: ApiEnvelope<{ job: OntologyBuildJob }>;
  try {
    payload = (await response.json()) as ApiEnvelope<{ job: OntologyBuildJob }>;
  } catch {
    payload = {};
  }
  if (!response.ok || !payload.data) {
    throw new ApiError(payloadMessage(payload, response.status), response.status);
  }
  return payload.data.job;
}

export function getOntologyBuildJob(
  jobId: string,
  options?: RequestOptions
): Promise<OntologyBuildJob> {
  return request<{ job: OntologyBuildJob }>(
    `/api/nl2sql/ontology-build/${encodeURIComponent(jobId)}`,
    "GET",
    undefined,
    options
  ).then((data) => data.job);
}

export function listProfileOntologyProposals(
  profileId: string,
  options?: RequestOptions
): Promise<OntologyProposal[]> {
  return request<{ proposals: OntologyProposal[] }>(
    `/api/nl2sql/profiles/${encodeURIComponent(profileId)}/ontology-proposals`,
    "GET",
    undefined,
    options
  ).then((data) => data.proposals ?? []);
}

export function acceptOntologyProposal(
  proposalId: string,
  options?: RequestOptions
): Promise<OntologyProposalReviewData> {
  return request(
    `/api/nl2sql/ontology/proposals/${encodeURIComponent(proposalId)}/accept`,
    "POST",
    undefined,
    options
  );
}

export function rejectOntologyProposal(
  proposalId: string,
  options?: RequestOptions
): Promise<OntologyProposalReviewData> {
  return request(
    `/api/nl2sql/ontology/proposals/${encodeURIComponent(proposalId)}/reject`,
    "POST",
    undefined,
    options
  );
}

export interface OntologyProposalBatchReviewData {
  proposals: OntologyProposal[];
  draft: OntologyProposalReviewData["draft"];
}

export function acceptOntologyProposalsBatch(
  proposalIds: string[],
  options?: RequestOptions
): Promise<OntologyProposalBatchReviewData> {
  return request(
    "/api/nl2sql/ontology/proposals/batch-accept",
    "POST",
    { proposal_ids: proposalIds },
    options
  );
}

export function listOntologyRevisions(
  options?: RequestOptions
): Promise<{ revisions: OntologyRevision[]; active_revision_id: string }> {
  return request("/api/nl2sql/ontology/revisions", "GET", undefined, options);
}

export function createOntologyRevisionDraft(
  revisionId: string,
  baseEtag: string,
  nodeUpserts: OntologyGraph["nodes"],
  options?: RequestOptions
): Promise<OntologyGraph> {
  return request(
    `/api/nl2sql/ontology/revisions/${encodeURIComponent(revisionId)}/drafts`,
    "POST",
    {
      base_etag: baseEtag,
      note: "業務モデル画面で意味定義を編集",
      node_upserts: nodeUpserts,
      edge_upserts: [],
      remove_node_ids: [],
      remove_edge_ids: [],
    },
    options
  );
}

export function publishOntologyRevision(
  revisionId: string,
  etag: string,
  options?: RequestOptions
): Promise<OntologyPublishJob> {
  return request<{ job: OntologyPublishJob }>(
    `/api/nl2sql/ontology/revisions/${encodeURIComponent(revisionId)}/publish`,
    "POST",
    { etag },
    { ...options, ifMatch: etag }
  ).then((data) => data.job);
}

export function getOntologyPublishJob(
  jobId: string,
  options?: RequestOptions
): Promise<OntologyPublishJob> {
  return request<{ job: OntologyPublishJob }>(
    `/api/nl2sql/ontology-publish/${encodeURIComponent(jobId)}`,
    "GET",
    undefined,
    options
  ).then((data) => data.job);
}

export function recommendOntologyProfiles(
  question: string,
  options?: RequestOptions
): Promise<OntologyProfileRecommendation> {
  return request<{ recommendation: OntologyProfileRecommendation }>(
    "/api/nl2sql/ontology/profile-recommendations",
    "POST",
    { question, limit: 3 },
    options
  ).then((data) => data.recommendation);
}

export function confirmOntologyProfileRecommendation(
  recommendationId: string,
  selectedProfileId: string,
  selectedRevisionId: string,
  options?: RequestOptions
): Promise<{ recommendation: OntologyProfileRecommendation; confirmation_token: string }> {
  return request(
    `/api/nl2sql/ontology/profile-recommendations/${encodeURIComponent(recommendationId)}/confirm`,
    "POST",
    {
      selected_profile_id: selectedProfileId,
      selected_revision_id: selectedRevisionId,
    },
    options
  );
}

export function fetchProfileOntologyMermaid(
  profileId: string,
  options?: RequestOptions
): Promise<{ mermaid: string }> {
  return request(
    `/api/nl2sql/profiles/${encodeURIComponent(profileId)}/ontology-view/mermaid`,
    "GET",
    undefined,
    options
  );
}
