import { Button } from "@/components/ui/button";
import { DisclosureChevron } from "@/components/ui/disclosure-chevron";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Ban,
  BookOpenCheck,
  ClipboardCopy,
  FileText,
  PencilLine,
  RefreshCw,
  Save,
  Sparkles,
  UploadCloud,
  X,
} from "lucide-react";

import { Banner, toast } from "@engchina/production-ready-ui";

import { StatusBadge } from "@/components/ui/status-badge";

import { TimedLoadingState } from "@/components/ProcessingState";
import { ContentActionBar } from "@/components/ContentActionBar";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { FileDropzone } from "@/components/ui/file-dropzone";
import { PageNotice, usePageNotice } from "@/components/page-notice";
import { isAbortError } from "@/lib/api";
import { t } from "@/lib/i18n";
import { toastError } from "@/lib/toast";
import { mergeUniqueFiles } from "@/lib/file-dropzone";
import { formatBytes, formatDateTime } from "@/lib/format";
import { elapsedMsBetween, formatElapsedClock } from "@/lib/operationTiming";
import { API_TIMEOUT_MS } from "@/lib/requestPolicy";
import {
  tabularFileFormatConfig,
  type TabularFileFormatConfig,
} from "@/lib/tabular-file-formats";
import { ManagementTabs } from "../components/DbAdminShared";
import {
  DbManagementLoadingSkeleton,
  DbObjectPanelHeader,
} from "../components/DbObjectManagementShared";
import {
  WorkflowProgressStrip,
  type WorkflowProgressStepStatus,
  type WorkflowProgressTone,
} from "../components/WorkflowProgressStrip";
import {
  ApiError,
  cancelOntologyBuildJob,
  getOntologyBuildJob,
  getOntologyMarkdownState,
  getOntologyPublishJob,
  listOntologyBuildJobs,
  listOntologySourceDocuments,
  publishOntologyRevision,
  retryOntologyBuildJob,
  saveOntologyMarkdownDraft,
  startOntologyBuild,
} from "./api";
import type {
  OntologyBuildJob,
  OntologyBuildStep,
  OntologyMarkdownState,
  OntologyPublishJob,
  OntologyRevision,
  OntologySourceDocument,
} from "./types";

const POLL_INTERVAL_MS = 1000;
const ONTOLOGY_BUILD_LONG_RUNNING_GRACE_MS = API_TIMEOUT_MS.longRunningJob;
// 長時間 job は一時的な状態取得失敗でも監視を継続する(404 は即終端)。
const MAX_POLL_FAILURES = Math.ceil(ONTOLOGY_BUILD_LONG_RUNNING_GRACE_MS / POLL_INTERVAL_MS);
const MARKDOWN_DRAFT_STALE_MS = ONTOLOGY_BUILD_LONG_RUNNING_GRACE_MS;
const ONTOLOGY_SOURCE_FILE_FORMATS: TabularFileFormatConfig = {
  accept: ".pdf,.docx,.txt,.md,.csv,.xlsx,.xls,.xlsm",
  formatLabel: ".PDF / .DOCX / .TXT / .MD / .CSV / .XLSX / .XLS / .XLSM",
};
const ONTOLOGY_SOURCE_FILE_MAX_COUNT = 5;
const ONTOLOGY_QA_FILE_FORMATS = tabularFileFormatConfig([".xlsm"]);
const textareaClass =
  "min-h-24 w-full resize-y rounded-md border border-border bg-card px-3 py-2 text-sm leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40";
const markdownTextareaClass =
  "min-h-[22rem] w-full resize-y rounded-md border border-border bg-code p-3 font-mono text-xs leading-6 text-code-fg outline-none transition-colors placeholder:text-muted focus:border-primary focus:ring-2 focus:ring-ring/40 disabled:cursor-not-allowed disabled:opacity-70";
type MarkdownTab = "draft" | "published";
type MarkdownStateApplyReason = "profile-load" | "background" | "build" | "save" | "publish";

interface ApplyMarkdownStateOptions {
  reason?: MarkdownStateApplyReason;
  fallbackDraftMarkdown?: string;
}

interface LocalSavedDraft {
  revisionId: string;
  etag: string;
  markdown: string;
}

const MARKDOWN_DRAFT_REFRESH_SIGNAL_PATTERN =
  /(?:Draft\s*revision.*保存しました|下書き\s*revision.*保存しました|Markdown\s*(?:artifact|成果物).*保存しました|Markdown\s*(?:Draft|下書き).*生成しました)/iu;

function formatElapsed(startIso: string | null | undefined, endIso: string | null | undefined, now: number): string {
  const elapsed = elapsedMsBetween(startIso, endIso, now);
  return elapsed === null ? "" : formatElapsedClock(elapsed);
}

function formatEventTime(iso: string): string {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime())
    ? ""
    : parsed.toLocaleTimeString("ja-JP", { hour12: false });
}

type BuildEvent = NonNullable<OntologyBuildJob["events"]>[number];

interface BuildEventAssignment {
  event: BuildEvent;
  index: number;
}

const SUPPRESSED_BUILD_EVENT_PREFIXES = [
  "構築リクエストを受け付けました",
  "AI オントロジー構築を開始しました",
  "構築が完了しました",
];

function parseTimeMs(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const parsed = new Date(iso).getTime();
  return Number.isNaN(parsed) ? null : parsed;
}

function eventBelongsToStep(eventMs: number | null, step: OntologyBuildStep): boolean {
  if (eventMs === null) return false;
  const startedAtMs = parseTimeMs(step.started_at);
  if (startedAtMs === null || eventMs < startedAtMs) return false;
  const finishedAtMs = parseTimeMs(step.finished_at);
  return finishedAtMs === null || eventMs <= finishedAtMs;
}

function normalizedProgressText(value: string | null | undefined): string {
  return (value ?? "")
    .normalize("NFKC")
    .replace(/\s+/gu, "")
    .replace(/[。．.…]+$/gu, "");
}

function markdownStateHasDraft(
  state: OntologyMarkdownState | null,
  draftMarkdown: string,
  draftRevision: OntologyRevision | null
): boolean {
  return Boolean(draftRevision || state?.draft_etag || draftMarkdown.trim());
}

function buildJobMarkdownSignalMessages(job: OntologyBuildJob): string[] {
  // 新 backend は機械可読コード MARKDOWN_DRAFT_UPDATED を付与する。
  // 文言正規表現は旧 job(コード無し)向けの互換フォールバック。
  const codedSignals = [
    ...job.steps.filter((step) => step.code === "MARKDOWN_DRAFT_UPDATED").map(
      (step) => step.detail_ja ?? ""
    ),
    ...(job.events ?? [])
      .filter((event) => event.code === "MARKDOWN_DRAFT_UPDATED")
      .map((event) => event.message_ja),
  ].filter(Boolean);
  if (codedSignals.length > 0) return codedSignals;
  return [
    ...job.steps.map((step) => step.detail_ja ?? ""),
    ...(job.events ?? []).map((event) => event.message_ja),
  ].filter((message) => MARKDOWN_DRAFT_REFRESH_SIGNAL_PATTERN.test(message));
}

function buildJobDraftRefreshSignature(job: OntologyBuildJob): string {
  const signalMessages = buildJobMarkdownSignalMessages(job);
  const hasDraftSignal = Boolean(
    job.draft_revision_id ||
      job.draft_etag ||
      job.markdown_output?.trim() ||
      signalMessages.length > 0
  );
  if (!hasDraftSignal) return "";
  const latestSignal = signalMessages[signalMessages.length - 1] ?? "";
  return [
    job.id,
    job.draft_revision_id ?? "",
    job.draft_etag ?? "",
    String(job.markdown_output?.length ?? 0),
    latestSignal,
    String(job.events?.length ?? 0),
  ].join("|");
}

function shouldSuppressBuildEvent(event: BuildEvent): boolean {
  const message = event.message_ja.trim();
  return SUPPRESSED_BUILD_EVENT_PREFIXES.some((prefix) => message.startsWith(prefix));
}

function eventDuplicatesStepDetail(event: BuildEvent, step: OntologyBuildStep): boolean {
  const detail = normalizedProgressText(step.detail_ja);
  if (!detail) return false;
  return normalizedProgressText(event.message_ja) === detail;
}

function stepIndexForEventMessage(
  steps: OntologyBuildStep[],
  message: string
): number | null {
  const matchers: Array<[OntologyBuildStep["name"], (value: string) => boolean]> = [
    [
      "schema_context",
      (value) => /スキーマ情報|DB から profile 範囲|DB schema|schema_context/iu.test(value),
    ],
    ["schema_naming", (value) => /業務エンティティ命名/iu.test(value)],
    ["qa_extraction", (value) => /Q\/A|QA|正解 SQL/iu.test(value)],
    ["text_extraction", (value) => /業務説明/iu.test(value)],
    [
      "proposal_registration",
      (value) =>
        /Markdown Draft|Markdown 下書き|Draft revision|下書き revision|Markdown artifact|Markdown 成果物|構築 job の完了状態/iu.test(
          value
        ),
    ],
    ["source_extraction", (value) => /構築資料|資料|source document/iu.test(value)],
  ];
  for (const [name, matcher] of matchers) {
    if (!matcher(message)) continue;
    const index = steps.findIndex((step) => step.name === name);
    if (index >= 0) return index;
  }
  return null;
}

function stepIndexForEventTime(job: OntologyBuildJob, eventMs: number | null): number | null {
  if (job.steps.length === 0 || eventMs === null) return null;
  const directIndex = job.steps.findIndex((step) => eventBelongsToStep(eventMs, step));
  if (directIndex >= 0) return directIndex;

  let previousTimedIndex = -1;
  for (const [index, step] of job.steps.entries()) {
    const startedAtMs = parseTimeMs(step.started_at);
    const finishedAtMs = parseTimeMs(step.finished_at);
    if (startedAtMs !== null && eventMs < startedAtMs) {
      return previousTimedIndex >= 0 ? previousTimedIndex : index;
    }
    const stepBoundaryMs = finishedAtMs ?? startedAtMs;
    if (stepBoundaryMs !== null && eventMs >= stepBoundaryMs) {
      previousTimedIndex = index;
    }
  }

  return previousTimedIndex >= 0 ? previousTimedIndex : job.steps.length - 1;
}

function groupBuildEvents(job: OntologyBuildJob): Map<number, BuildEventAssignment[]> {
  const byStepIndex = new Map<number, BuildEventAssignment[]>();
  for (const [index, event] of (job.events ?? []).entries()) {
    if (shouldSuppressBuildEvent(event)) continue;
    const eventMs = parseTimeMs(event.at);
    // 新 backend はイベントに帰属ステップ(event.step)を付与する。
    // 文言/時刻ベースの推定は旧 job(step 無し)向けの互換フォールバック。
    const codedStepIndex = event.step
      ? job.steps.findIndex((step) => step.name === event.step)
      : -1;
    const stepIndex =
      (codedStepIndex >= 0 ? codedStepIndex : null) ??
      stepIndexForEventMessage(job.steps, event.message_ja) ??
      stepIndexForEventTime(job, eventMs);
    if (stepIndex === null) continue;
    const step = job.steps[stepIndex];
    if (!step || eventDuplicatesStepDetail(event, step)) continue;
    const assignment = { event, index };
    const stepEvents = byStepIndex.get(stepIndex) ?? [];
    if (
      stepEvents.some(
        ({ event: existing }) =>
          normalizedProgressText(existing.message_ja) === normalizedProgressText(event.message_ja)
      )
    ) {
      continue;
    }
    stepEvents.push(assignment);
    byStepIndex.set(stepIndex, stepEvents);
  }
  return byStepIndex;
}

function latestJobActivityMs(job: OntologyBuildJob): number | null {
  const values = [
    parseTimeMs(job.started_at),
    parseTimeMs(job.created_at),
    ...(job.events ?? []).map((event) => parseTimeMs(event.at)),
    ...job.steps.map((step) => parseTimeMs(step.started_at)),
  ].filter((value): value is number => value !== null);
  return values.length > 0 ? Math.max(...values) : null;
}

function markdownDraftGenerationIsStale(job: OntologyBuildJob, now: number): boolean {
  if (job.status !== "running") return false;
  const markdownStep = job.steps.find((step) => step.name === "proposal_registration");
  if (markdownStep?.status !== "running") return false;
  const lastActivityMs = latestJobActivityMs(job);
  return lastActivityMs !== null && now - lastActivityMs >= MARKDOWN_DRAFT_STALE_MS;
}

function BuildEventRows({ events }: { events: BuildEventAssignment[] }) {
  return (
    <>
      {events.map(({ event, index }) => (
        <li
          key={`${event.at}-${index}`}
          className="grid grid-cols-[auto_minmax(0,1fr)] gap-2 text-xs leading-5"
        >
          <span className="tabular-nums text-muted">
            {formatEventTime(event.at)}
          </span>
          <span className="break-words text-foreground">{event.message_ja}</span>
        </li>
      ))}
    </>
  );
}

function buildStatusVariant(status: OntologyBuildJob["status"]) {
  if (status === "succeeded") return "success" as const;
  if (status === "succeeded_with_warnings") return "warning" as const;
  if (status === "failed") return "danger" as const;
  if (status === "cancelled") return "warning" as const;
  return "pending" as const;
}

function buildProgressTone(status: OntologyBuildJob["status"]): WorkflowProgressTone {
  if (status === "succeeded") return "success";
  if (status === "succeeded_with_warnings") return "success";
  if (status === "failed") return "danger";
  if (status === "cancelled") return "neutral";
  return "active";
}

function buildProgressMessage(status: OntologyBuildJob["status"]) {
  if (status === "succeeded") return t("profiles.ontologyBuild.progress.done");
  if (status === "succeeded_with_warnings")
    return t("profiles.ontologyBuild.progress.doneWithWarnings");
  if (status === "failed") return t("profiles.ontologyBuild.progress.failed");
  if (status === "cancelled") return t("profiles.ontologyBuild.progress.cancelled");
  if (status === "running") return t("profiles.ontologyBuild.progress.running");
  return t("profiles.ontologyBuild.progress.pending");
}

function normalizeBuildStepStatus(status: OntologyBuildStep["status"]): WorkflowProgressStepStatus {
  if (status === "succeeded") return "done";
  if (status === "failed") return "error";
  if (status === "running") return "running";
  if (status === "skipped") return "skipped";
  return "pending";
}

function effectiveBuildStepStatus(
  jobStatus: OntologyBuildJob["status"],
  stepStatus: OntologyBuildStep["status"]
): OntologyBuildStep["status"] {
  if (stepStatus !== "running" && stepStatus !== "pending") return stepStatus;
  if (jobStatus === "succeeded" || jobStatus === "succeeded_with_warnings") return "succeeded";
  if (jobStatus === "failed") return "failed";
  if (jobStatus === "cancelled") return "skipped";
  return stepStatus;
}

function sourceStatusVariant(
  status: OntologySourceDocument["status"]
): "danger" | "info" | "pending" | "success" {
  if (status === "failed") return "danger";
  if (status === "extracted") return "success";
  if (status === "extracting") return "pending";
  return "info";
}

function sourceDocumentMeta(source: OntologySourceDocument): string {
  return [
    formatBytes(source.size_bytes ?? null),
    formatDateTime(source.updated_at ?? source.created_at),
  ].join(" · ");
}

function SavedSourceDocumentsList({
  documents,
  loading,
}: {
  documents: OntologySourceDocument[];
  loading: boolean;
}) {
  return (
    <section
      className="grid min-w-0 gap-2 rounded-md border border-border bg-card px-3 py-2"
      aria-label={t("profiles.ontologyBuild.savedFiles")}
      data-testid="ontology-build-saved-files"
    >
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <h4 className="text-sm font-semibold text-foreground">
            {t("profiles.ontologyBuild.savedFiles")}
          </h4>
          <p className="text-xs leading-5 text-muted">
            {t("profiles.ontologyBuild.savedFilesHint")}
          </p>
        </div>
        {loading ? (
          <StatusBadge variant="pending" label={t("common.loading")} />
        ) : null}
      </div>
      {documents.length > 0 ? (
        <ul className="grid gap-1" aria-label={t("profiles.ontologyBuild.savedFilesList")}>
          {documents.map((source) => (
            <li
              key={source.id}
              className="grid min-w-0 gap-2 rounded-md border border-border bg-background px-3 py-2 text-sm sm:grid-cols-[minmax(0,1fr)_auto]"
            >
              <span className="min-w-0">
                <span className="block break-all font-semibold text-foreground">
                  {source.filename}
                </span>
                <span className="block text-xs leading-5 text-muted">
                  {sourceDocumentMeta(source)}
                </span>
              </span>
              <span className="flex flex-wrap items-center gap-2 sm:justify-end">
                <StatusBadge
                  variant="neutral"
                  label={t(
                    `profiles.ontologyBuild.sourceRole.${source.source_role ?? "source"}`
                  )}
                />
                <StatusBadge
                  variant={sourceStatusVariant(source.status)}
                  label={t(`profiles.ontologyBuild.sourceStatus.${source.status}`)}
                />
                {(source.extracted_chunk_count ?? 0) > 0 ? (
                  <span className="text-xs tabular-nums text-muted">
                    {t("profiles.ontologyBuild.sourceChunks", {
                      count: source.extracted_chunk_count ?? 0,
                    })}
                  </span>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      ) : loading ? (
        <p className="text-sm text-muted" data-testid="ontology-build-saved-files-loading">
          {t("profiles.ontologyBuild.savedFilesLoading")}
        </p>
      ) : (
        <p className="text-sm text-muted" data-testid="ontology-build-saved-files-empty">
          {t("profiles.ontologyBuild.savedFilesEmpty")}
        </p>
      )}
    </section>
  );
}

export interface OntologyBuildSectionProps {
  profileId: string | null;
  hasProfileSchemaInput: boolean;
  onPublished?: () => void | Promise<void>;
  onMarkdownStateChange?: (state: OntologyMarkdownState | null) => void;
  onRefreshSchema?: () => void | Promise<void>;
  refreshingSchema?: boolean;
}

export function OntologyBuildSection({
  profileId,
  hasProfileSchemaInput,
  onPublished,
  onMarkdownStateChange,
  onRefreshSchema,
  refreshingSchema = false,
}: OntologyBuildSectionProps) {
  const [businessText, setBusinessText] = useState("");
  const [qaFile, setQaFile] = useState<File | null>(null);
  const [sourceFiles, setSourceFiles] = useState<File[]>([]);
  const [sourceFilesError, setSourceFilesError] = useState("");
  const [savedSourceDocuments, setSavedSourceDocuments] = useState<OntologySourceDocument[]>([]);
  const [savedSourceDocumentsLoading, setSavedSourceDocumentsLoading] = useState(false);
  const [job, setJob] = useState<OntologyBuildJob | null>(null);
  const [markdownState, setMarkdownState] = useState<OntologyMarkdownState | null>(null);
  const [markdownLoading, setMarkdownLoading] = useState(false);
  const [markdownError, setMarkdownError] = useState("");
  const [draftMarkdown, setDraftMarkdown] = useState("");
  const [draftDirty, setDraftDirty] = useState(false);
  const [activeMarkdownTab, setActiveMarkdownTab] = useState<MarkdownTab>("draft");
  const [draftRevision, setDraftRevision] = useState<OntologyRevision | null>(null);
  const [publishJob, setPublishJob] = useState<OntologyPublishJob | null>(null);
  const [progressCollapsed, setProgressCollapsed] = useState(false);
  const { notice, showNotice, clearNotice } = usePageNotice();
  const confirm = useConfirm();
  const [busy, setBusy] = useState("");
  // 実行中ステップの経過秒を更新するための現在時刻(ポーリングと同じ周期で更新)
  const [nowTick, setNowTick] = useState(() => Date.now());
  // プロファイル切替後に in-flight 応答が旧プロファイルの状態を上書きしないためのガード
  const profileIdRef = useRef(profileId);
  const markdownStateRef = useRef<OntologyMarkdownState | null>(null);
  const draftMarkdownRef = useRef("");
  const draftDirtyRef = useRef(false);
  const draftRevisionRef = useRef<OntologyRevision | null>(null);
  const localSavedDraftRef = useRef<LocalSavedDraft | null>(null);
  const sourceDocumentsRequestIdRef = useRef(0);
  const sourceDocumentsLoadControllerRef = useRef<AbortController | null>(null);
  const markdownRequestIdRef = useRef(0);
  const markdownLoadControllerRef = useRef<AbortController | null>(null);
  const buildMarkdownRefreshSignatureRef = useRef("");
  const pollInFlightRef = useRef(false);
  const pollFailureCountRef = useRef(0);
  const publishPollInFlightRef = useRef(false);
  const publishPollFailureCountRef = useRef(0);
  // 終端(完了/失敗)通知を job ごとに一度だけ出す
  const terminalHandledRef = useRef<string | null>(null);
  const publishTerminalHandledRef = useRef<string | null>(null);

  const jobRunning = job !== null && (job.status === "queued" || job.status === "running");
  const publishRunning =
    publishJob !== null &&
    ["queued", "materializing", "validating"].includes(publishJob.status);
  const jobId = job?.id ?? null;
  const publishJobId = publishJob?.id ?? null;
  // 新 backend は機械可読 error_code(SCHEMA_SCOPE_*)を返す。文言一致は旧 job 互換。
  const schemaScopeFailure =
    job?.status === "failed" &&
    (job.error_code === "SCHEMA_SCOPE_UNRESOLVED" ||
      job.steps.some(
        (step) =>
          step.name === "schema_context" &&
          step.status === "failed" &&
          (step.code === "SCHEMA_SCOPE_EMPTY" || step.code === "SCHEMA_SCOPE_AMBIGUOUS")
      ) ||
      job.error_message_ja?.includes(
        "profile の対象オブジェクトを DB schema catalog に解決できません"
      ) ||
      job.steps.some(
        (step) =>
          step.name === "schema_context" &&
          step.status === "failed" &&
          (step.detail_ja?.includes("profile 範囲に DB 表・ビューがありません") ||
            step.detail_ja?.includes("profile 範囲の DB object が曖昧です"))
      ));
  const groupedEvents = useMemo(() => (job ? groupBuildEvents(job) : null), [job]);
  const markdownDraftStale = job ? markdownDraftGenerationIsStale(job, nowTick) : false;
  const unscopedBuildError =
    job?.status === "failed" && !schemaScopeFailure && job.error_message_ja
      ? `${job.error_message_ja} ${t("profiles.ontologyBuild.error.retryHint")}`
      : "";
  const publishedMarkdown = markdownState?.published_markdown ?? "";
  const publishedRevision = markdownState?.published_revision ?? null;
  const publishedAt = publishedRevision?.published_at ?? markdownState?.published_at ?? null;
  const draftDisplayVersion = markdownState?.draft_version ?? draftRevision?.version;
  const publishedDisplayVersion = markdownState?.published_version ?? publishedRevision?.version;
  const draftRevisionMeta = draftRevision
    ? t("profiles.ontologyBuild.markdownTabVersion", {
        version: String(draftDisplayVersion),
      })
    : t("profiles.ontologyBuild.markdownDraftMissing");
  const publishedRevisionMeta = publishedRevision
    ? t("profiles.ontologyBuild.markdownTabVersion", {
        version: String(publishedDisplayVersion),
      })
    : t("profiles.ontologyBuild.markdownPublishedMissing");
  // 公開済み Markdown が無い(revision だけ公開済み)場合は公開日時を出さない。
  // 「公開済み Markdown はまだありません」と日時が並ぶ矛盾表示を防ぐ。
  const publishedAtMeta =
    publishedAt && publishedMarkdown.trim()
      ? t("profiles.ontologyBuild.markdownPublishedAt", {
          date: formatDateTime(publishedAt),
        })
      : "";

  const markdownTabs = useMemo(
    () => [
      {
        id: "draft" as const,
        label: t("profiles.ontologyBuild.markdownTab.draft"),
        icon: PencilLine,
        ariaLabel: t("profiles.ontologyBuild.markdownTabAria.draft"),
        meta: draftRevisionMeta,
        metaTestId: "ontology-markdown-tab-draft-meta",
      },
      {
        id: "published" as const,
        label: t("profiles.ontologyBuild.markdownTab.published"),
        icon: BookOpenCheck,
        ariaLabel: t("profiles.ontologyBuild.markdownTabAria.published"),
        meta: publishedRevisionMeta,
        metaTestId: "ontology-markdown-tab-published-meta",
      },
    ],
    [draftRevisionMeta, publishedRevisionMeta]
  );
  const activeMarkdown =
    activeMarkdownTab === "draft" ? draftMarkdown : publishedMarkdown;
  const hasDraftRevision = draftRevision?.status === "draft";

  const applyMarkdownState = useCallback((
    next: OntologyMarkdownState,
    options: ApplyMarkdownStateOptions = {}
  ) => {
    const reason = options.reason ?? "background";
    const currentState = markdownStateRef.current;
    const currentDraftMarkdown = draftMarkdownRef.current;
    const currentDraftDirty = draftDirtyRef.current;
    const currentDraftRevision = draftRevisionRef.current;
    const currentRevisionId = currentDraftRevision?.id ?? "";
    const incomingDraftMarkdown = next.draft_markdown ?? "";
    const incomingDraftRevision = next.draft_revision;
    const incomingRevisionId = incomingDraftRevision?.id ?? "";
    const incomingPublishedRevisionId = next.published_revision?.id ?? "";
    const incomingHasDraft = markdownStateHasDraft(
      next,
      incomingDraftMarkdown,
      incomingDraftRevision
    );
    const currentHasDraft = markdownStateHasDraft(
      currentState,
      currentDraftMarkdown,
      currentDraftRevision
    );
    const incomingIsNewBuildRevision =
      reason === "build" &&
      incomingHasDraft &&
      (!currentRevisionId || incomingRevisionId !== currentRevisionId);
    const localSavedDraft = localSavedDraftRef.current;
    const staleAfterLocalSave =
      reason !== "profile-load" &&
      reason !== "save" &&
      localSavedDraft !== null &&
      currentRevisionId !== "" &&
      incomingRevisionId === currentRevisionId &&
      localSavedDraft.revisionId === currentRevisionId &&
      currentState?.draft_etag === localSavedDraft.etag &&
      currentDraftMarkdown === localSavedDraft.markdown &&
      next.draft_etag !== localSavedDraft.etag;
    const currentDraftWasPublished =
      reason === "publish" &&
      currentRevisionId !== "" &&
      incomingPublishedRevisionId === currentRevisionId;
    const preserveCurrentDraft =
      reason !== "profile-load" &&
      reason !== "save" &&
      !currentDraftWasPublished &&
      currentHasDraft &&
      (!incomingHasDraft ||
        staleAfterLocalSave ||
        (currentDraftDirty && !incomingIsNewBuildRevision));
    const savedDraftMarkdown =
      reason === "save" &&
      options.fallbackDraftMarkdown !== undefined &&
      !incomingDraftMarkdown.trim()
        ? options.fallbackDraftMarkdown
        : incomingDraftMarkdown;
    const reconciled: OntologyMarkdownState = preserveCurrentDraft
      ? {
          ...next,
          draft_markdown: currentDraftMarkdown,
          draft_revision: currentDraftRevision,
          draft_version: currentState?.draft_version ?? next.draft_version,
          draft_etag: currentState?.draft_etag ?? "",
        }
      : reason === "save"
        ? { ...next, draft_markdown: savedDraftMarkdown }
        : next;
    const nextDraftDirty = preserveCurrentDraft ? currentDraftDirty : false;

    markdownStateRef.current = reconciled;
    draftRevisionRef.current = reconciled.draft_revision;
    draftMarkdownRef.current = reconciled.draft_markdown ?? "";
    draftDirtyRef.current = nextDraftDirty;
    if (reason === "save") {
      localSavedDraftRef.current = {
        revisionId: reconciled.draft_revision?.id ?? "",
        etag: reconciled.draft_etag,
        markdown: reconciled.draft_markdown ?? "",
      };
    } else if (
      reason === "profile-load" ||
      incomingIsNewBuildRevision ||
      currentDraftWasPublished
    ) {
      localSavedDraftRef.current = null;
    }

    setMarkdownState(reconciled);
    setDraftRevision(reconciled.draft_revision);
    setDraftMarkdown(reconciled.draft_markdown ?? "");
    setDraftDirty(nextDraftDirty);
    onMarkdownStateChange?.(reconciled);
  }, [onMarkdownStateChange]);

  const refreshSourceDocuments = useCallback(async (targetProfileId: string) => {
    const requestId = sourceDocumentsRequestIdRef.current + 1;
    sourceDocumentsRequestIdRef.current = requestId;
    sourceDocumentsLoadControllerRef.current?.abort();
    const controller = new AbortController();
    sourceDocumentsLoadControllerRef.current = controller;
    setSavedSourceDocumentsLoading(true);
    try {
      const documents = await listOntologySourceDocuments(targetProfileId, 20, {
        signal: controller.signal,
      });
      if (
        profileIdRef.current === targetProfileId &&
        sourceDocumentsRequestIdRef.current === requestId
      ) {
        setSavedSourceDocuments(documents);
      }
    } catch (err) {
      if (isAbortError(err)) return;
      if (
        profileIdRef.current === targetProfileId &&
        sourceDocumentsRequestIdRef.current === requestId
      ) {
        setSavedSourceDocuments([]);
      }
    } finally {
      if (
        profileIdRef.current === targetProfileId &&
        sourceDocumentsRequestIdRef.current === requestId
      ) {
        setSavedSourceDocumentsLoading(false);
      }
      if (sourceDocumentsLoadControllerRef.current === controller) {
        sourceDocumentsLoadControllerRef.current = null;
      }
    }
  }, []);

  const refreshMarkdown = useCallback(async (
    targetProfileId: string,
    options: ApplyMarkdownStateOptions = {}
  ) => {
    const requestId = markdownRequestIdRef.current + 1;
    markdownRequestIdRef.current = requestId;
    markdownLoadControllerRef.current?.abort();
    const controller = new AbortController();
    markdownLoadControllerRef.current = controller;
    setMarkdownLoading(true);
    setMarkdownError("");
    try {
      const next = await getOntologyMarkdownState(targetProfileId, {
        signal: controller.signal,
      });
      if (
        profileIdRef.current === targetProfileId &&
        markdownRequestIdRef.current === requestId
      ) {
        applyMarkdownState(next, options);
      }
    } catch (err) {
      if (isAbortError(err)) return;
      if (
        profileIdRef.current === targetProfileId &&
        markdownRequestIdRef.current === requestId
      ) {
        setMarkdownError(t("profiles.ontologyBuild.markdownLoadError"));
      }
    } finally {
      if (
        profileIdRef.current === targetProfileId &&
        markdownRequestIdRef.current === requestId
      ) {
        setMarkdownLoading(false);
      }
      if (markdownLoadControllerRef.current === controller) {
        markdownLoadControllerRef.current = null;
      }
    }
  }, [applyMarkdownState]);

  const cancelMarkdownLoad = useCallback(() => {
    const controller = markdownLoadControllerRef.current;
    if (!controller) return;
    markdownLoadControllerRef.current = null;
    markdownRequestIdRef.current += 1;
    controller.abort();
    setMarkdownLoading(false);
  }, []);

  const applyBuildJobMarkdownOutput = useCallback((next: OntologyBuildJob) => {
    const markdownOutput = next.markdown_output ?? "";
    if (!markdownOutput.trim()) return;
    const currentRevisionId = draftRevisionRef.current?.id ?? "";
    const outputRevisionId = next.draft_revision_id ?? "";
    const outputIsNewRevision = Boolean(outputRevisionId && outputRevisionId !== currentRevisionId);
    if (draftDirtyRef.current && !outputIsNewRevision) return;
    if (outputIsNewRevision) {
      const currentState = markdownStateRef.current;
      const previewState = currentState
        ? {
            ...currentState,
            draft_markdown: markdownOutput,
            draft_revision: null,
            draft_etag: "",
          }
        : null;
      markdownStateRef.current = previewState;
      draftRevisionRef.current = null;
      setMarkdownState(previewState);
      setDraftRevision(null);
      onMarkdownStateChange?.(previewState);
    }
    draftMarkdownRef.current = markdownOutput;
    draftDirtyRef.current = false;
    setDraftMarkdown(markdownOutput);
    setDraftDirty(false);
  }, [onMarkdownStateChange]);

  useEffect(() => {
    profileIdRef.current = profileId;
    markdownStateRef.current = null;
    draftMarkdownRef.current = "";
    draftDirtyRef.current = false;
    draftRevisionRef.current = null;
    localSavedDraftRef.current = null;
    buildMarkdownRefreshSignatureRef.current = "";
    setJob(null);
    setMarkdownState(null);
    onMarkdownStateChange?.(null);
    setDraftMarkdown("");
    setDraftDirty(false);
    setActiveMarkdownTab("draft");
    setDraftRevision(null);
    setPublishJob(null);
    setProgressCollapsed(false);
    clearNotice();
    setMarkdownLoading(Boolean(profileId));
    setMarkdownError("");
    setBusinessText("");
    setQaFile(null);
    setSourceFiles([]);
    setSourceFilesError("");
    setSavedSourceDocuments([]);
    setSavedSourceDocumentsLoading(Boolean(profileId));
    const jobsController = new AbortController();
    if (profileId) {
      void refreshMarkdown(profileId, { reason: "profile-load" });
      void refreshSourceDocuments(profileId);
      // リロード/プロファイル切替後も直近 job を復元する(実行中なら進捗追跡を再開)
      listOntologyBuildJobs(profileId, 1, { signal: jobsController.signal })
        .then((jobs) => {
          if (jobsController.signal.aborted) return;
          const latest = jobs[0];
          if (!latest) return;
          if (latest.status === "queued" || latest.status === "running") {
            setJob(latest);
          } else if (
            latest.status === "succeeded" ||
            latest.status === "succeeded_with_warnings" ||
            latest.status === "failed" ||
            latest.status === "cancelled"
          ) {
            // 終端カード(+ Markdown)を復元するが、完了トーストは再通知しない
            terminalHandledRef.current = latest.id;
            setJob(latest);
          }
        })
        .catch(() => {
          // 直近 job は補助情報のため取得失敗で画面を止めない(新規実行は可能)
        });
    }
    return () => {
      markdownLoadControllerRef.current?.abort();
      markdownLoadControllerRef.current = null;
      sourceDocumentsLoadControllerRef.current?.abort();
      sourceDocumentsLoadControllerRef.current = null;
      jobsController.abort();
    };
  }, [onMarkdownStateChange, profileId, refreshMarkdown, refreshSourceDocuments]);

  // job ポーリング(1s)。完了で停止し、Markdown 下書きを更新する。
  // 依存は jobId(文字列)なので毎秒の setJob で interval は再生成されない。
  useEffect(() => {
    if (!jobRunning || !jobId || !profileId) return;
    pollFailureCountRef.current = 0;
    let cancelled = false;
    let currentController: AbortController | null = null;
    const timer = window.setInterval(() => {
      setNowTick(Date.now());
      if (pollInFlightRef.current) return; // 応答遅延時に GET を重ねない
      currentController = new AbortController();
      pollInFlightRef.current = true;
      getOntologyBuildJob(jobId, { signal: currentController.signal })
        .then((next) => {
          if (cancelled) return; // 古い応答で新しい状態を上書きしない
          pollFailureCountRef.current = 0;
          setJob(next);
          const terminal =
            next.status === "succeeded" ||
            next.status === "succeeded_with_warnings" ||
            next.status === "failed" ||
            next.status === "cancelled";
          const draftRefreshSignature = buildJobDraftRefreshSignature(next);
          applyBuildJobMarkdownOutput(next);
          if (
            !terminal &&
            draftRefreshSignature &&
            buildMarkdownRefreshSignatureRef.current !== draftRefreshSignature
          ) {
            buildMarkdownRefreshSignatureRef.current = draftRefreshSignature;
            void refreshMarkdown(profileId, { reason: "build" });
          }
          if (terminal && terminalHandledRef.current !== jobId) {
            terminalHandledRef.current = jobId;
            buildMarkdownRefreshSignatureRef.current = "";
            void refreshMarkdown(profileId, { reason: "build" });
            void refreshSourceDocuments(profileId);
            if (next.status === "succeeded") {
              toast.success(t("profiles.ontologyBuild.jobSucceeded"));
            } else if (next.status === "succeeded_with_warnings") {
              showNotice("warning", t("profiles.ontologyBuild.jobSucceededWithWarnings"));
            } else if (next.status === "cancelled") {
              showNotice("info", t("profiles.ontologyBuild.cancelled"));
            }
          }
        })
        .catch((err) => {
          if (cancelled || isAbortError(err)) return;
          // 永続 job 自体が削除・期限切れになった 404 は即終端する
          const notFound = err instanceof ApiError && err.status === 404;
          pollFailureCountRef.current += 1;
          if (notFound || pollFailureCountRef.current >= MAX_POLL_FAILURES) {
            setJob(null); // jobRunning=false → cleanup で interval 停止、実行ボタン復帰
            showNotice(
              "danger",
              t(
                notFound
                  ? "profiles.ontologyBuild.error.jobLost"
                  : "profiles.ontologyBuild.error.pollFailed"
              )
            );
            // Draft は永続化済みの場合があるため job が消えても再取得を試みる
            void refreshMarkdown(profileId, { reason: "background" });
          }
          // それ以外の一時的な失敗は次のポーリングで回復を試みる
        })
        .finally(() => {
          currentController = null;
          pollInFlightRef.current = false;
        });
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      currentController?.abort();
    };
  }, [applyBuildJobMarkdownOutput, jobRunning, jobId, profileId, refreshMarkdown, showNotice]);

  // publish job ポーリング(1s)。build 側と同じく in-flight ガードで GET を重ねず、
  // 依存は publishJobId(文字列)なので毎秒の setPublishJob で interval は再生成されない。
  useEffect(() => {
    if (!publishRunning || !publishJobId) return;
    publishPollFailureCountRef.current = 0;
    let cancelled = false;
    let currentController: AbortController | null = null;
    const timer = window.setInterval(() => {
      if (publishPollInFlightRef.current) return; // 応答遅延時に GET を重ねない
      currentController = new AbortController();
      publishPollInFlightRef.current = true;
      getOntologyPublishJob(publishJobId, { signal: currentController.signal })
        .then((next) => {
          if (cancelled) return; // 古い応答で新しい状態を上書きしない
          publishPollFailureCountRef.current = 0;
          setPublishJob(next);
          const terminal = next.status === "succeeded" || next.status === "failed";
          if (!terminal || publishTerminalHandledRef.current === publishJobId) return;
          publishTerminalHandledRef.current = publishJobId;
          if (next.status === "succeeded") {
            if (profileIdRef.current) {
              void refreshMarkdown(profileIdRef.current, { reason: "publish" });
            }
            toast.success(t("profiles.ontologyBuild.published"));
            void onPublished?.();
          } else {
            showNotice(
              "danger",
              next.error_message_ja || t("profiles.ontologyBuild.error.publish")
            );
          }
        })
        .catch((err) => {
          if (cancelled || isAbortError(err)) return;
          const notFound = err instanceof ApiError && err.status === 404;
          publishPollFailureCountRef.current += 1;
          if (notFound || publishPollFailureCountRef.current >= MAX_POLL_FAILURES) {
            showNotice(
              "danger",
              err instanceof Error ? err.message : t("profiles.ontologyBuild.error.publish")
            );
            setPublishJob(null);
          }
        })
        .finally(() => {
          currentController = null;
          publishPollInFlightRef.current = false;
        });
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      currentController?.abort();
    };
  }, [onPublished, publishJobId, publishRunning, refreshMarkdown, showNotice]);

  if (!profileId) {
    return (
      <section
        className="grid gap-3 rounded-md border border-border bg-card p-3"
        aria-label={t("profiles.ontologyBuild.title")}
        data-testid="profile-ontology-build"
      >
        <SectionHeading />
        <Banner severity="info">{t("profiles.ontologyBuild.requiresProfile")}</Banner>
      </section>
    );
  }

  const startBuild = async () => {
    const targetProfileId = profileId;
    const hasBusinessTextInput = businessText.trim().length > 0;
    const hasSourceFilesInput = sourceFiles.length > 0;
    if (sourceFiles.length > ONTOLOGY_SOURCE_FILE_MAX_COUNT) {
      setSourceFilesError(
        t("profiles.ontologyBuild.sourceFilesMaxExceeded", {
          count: ONTOLOGY_SOURCE_FILE_MAX_COUNT,
        })
      );
      return;
    }
    buildMarkdownRefreshSignatureRef.current = "";
    setProgressCollapsed(false);
    setBusy("start");
    clearNotice();
    try {
      const started = await startOntologyBuild(targetProfileId, {
        businessText,
        qaFile,
        sourceFiles,
        runSchemaNaming: hasProfileSchemaInput,
        runQaExtraction: qaFile !== null,
        runTextExtraction: hasBusinessTextInput || hasSourceFilesInput,
      });
      // プロファイル切替後に旧プロファイルの job を表示しない
      if (profileIdRef.current === targetProfileId) {
        setJob(started);
        void refreshSourceDocuments(targetProfileId);
      }
    } catch (err) {
      if (profileIdRef.current !== targetProfileId) return;
      const timedOut =
        err instanceof DOMException && (err.name === "TimeoutError" || err.name === "AbortError");
      showNotice(
        "danger",
        timedOut
          ? t("profiles.ontologyBuild.error.startTimeout")
          : err instanceof Error
            ? err.message
            : t("profiles.ontologyBuild.error.start")
      );
    } finally {
      setBusy("");
    }
  };

  /** failed/cancelled job を保存済み入力から再実行する(既存 retry API を利用)。 */
  const retryBuild = async () => {
    const targetJobId = jobId;
    if (!targetJobId || busy) return;
    setBusy("retry");
    try {
      const next = await retryOntologyBuildJob(targetJobId);
      // 新 job としてポーリング・終端通知・Draft 再取得の状態をリセットする
      terminalHandledRef.current = null;
      buildMarkdownRefreshSignatureRef.current = "";
      pollFailureCountRef.current = 0;
      setJob(next);
      clearNotice();
    } catch (err) {
      showNotice(
        "danger",
        err instanceof Error ? err.message : t("profiles.ontologyBuild.error.retry")
      );
    } finally {
      setBusy("");
    }
  };

  const cancelBuild = async () => {
    const targetJobId = jobId;
    if (!targetJobId) return;
    const ok = await confirm({
      title: t("profiles.ontologyBuild.cancelConfirm.title"),
      description: t("profiles.ontologyBuild.cancelConfirm.description"),
      confirmLabel: t("profiles.ontologyBuild.cancelConfirm.confirm"),
      tone: "danger",
      dismissOnOverlay: false,
    });
    if (!ok) return;
    setBusy("cancel");
    try {
      // 自前の中止はポーリング側で二重通知しない
      terminalHandledRef.current = targetJobId;
      const next = await cancelOntologyBuildJob(targetJobId);
      setJob(next);
      showNotice("info", t("profiles.ontologyBuild.cancelled"));
    } catch (err) {
      showNotice(
        "danger",
        err instanceof Error ? err.message : t("profiles.ontologyBuild.error.cancel")
      );
    } finally {
      setBusy("");
    }
  };

  const saveDraftMarkdown = async ({ silent = false }: { silent?: boolean } = {}) => {
    const currentMarkdownState = markdownStateRef.current;
    const currentDraftRevision = draftRevisionRef.current;
    if (!profileId || !currentMarkdownState?.draft_etag || currentDraftRevision?.status !== "draft") {
      showNotice("danger", t("profiles.ontologyBuild.error.saveDraftNoDraft"));
      return null;
    }
    if (!silent) setBusy("save-draft");
    clearNotice();
    const markdownToSave = draftMarkdownRef.current;
    const baseDraftEtag = currentMarkdownState.draft_etag;
    try {
      const next = await saveOntologyMarkdownDraft(profileId, {
        markdown: markdownToSave,
        base_etag: baseDraftEtag,
      });
      applyMarkdownState(next, {
        reason: "save",
        fallbackDraftMarkdown: markdownToSave,
      });
      if (!silent) toast.success(t("profiles.ontologyBuild.markdownSaved"));
      return next;
    } catch (err) {
      showNotice(
        "danger",
        err instanceof Error ? err.message : t("profiles.ontologyBuild.error.saveDraft")
      );
      return null;
    } finally {
      if (!silent) setBusy("");
    }
  };

  const publish = async () => {
    if (!draftRevision || !hasDraftRevision) return;
    setBusy("publish");
    clearNotice();
    try {
      let revisionToPublish = draftRevision;
      if (draftDirty) {
        const saved = await saveDraftMarkdown({ silent: true });
        if (!saved?.draft_revision) return;
        revisionToPublish = saved.draft_revision;
      }
      setPublishJob(await publishOntologyRevision(revisionToPublish.id, revisionToPublish.etag));
    } catch (err) {
      showNotice("danger", err instanceof Error ? err.message : t("profiles.ontologyBuild.error.publish"));
    } finally {
      setBusy("");
    }
  };

  const handleSourceFiles = (picked: File[]) => {
    const next = mergeUniqueFiles(sourceFiles, picked);
    if (next.length > ONTOLOGY_SOURCE_FILE_MAX_COUNT) {
      setSourceFilesError(
        t("profiles.ontologyBuild.sourceFilesMaxExceeded", {
          count: ONTOLOGY_SOURCE_FILE_MAX_COUNT,
        })
      );
      return;
    }
    setSourceFilesError("");
    setSourceFiles(next);
  };

  const copyMarkdownOutput = async () => {
    if (!activeMarkdown.trim()) return;
    try {
      await navigator.clipboard.writeText(activeMarkdown);
      toast.success(t("common.action.copied"));
    } catch {
      toastError(t("common.action.copyFailed"));
    }
  };

  return (
    <section
      className="grid min-w-0 gap-4 rounded-md border border-border bg-card p-4 shadow-sm"
      aria-label={t("profiles.ontologyBuild.title")}
      data-testid="profile-ontology-build"
    >
      <SectionHeading />
      <PageNotice notice={notice} />
      <section
        className="grid w-full min-w-0 content-start gap-4 rounded-md border border-border bg-background p-3"
        aria-label={t("profiles.ontologyBuild.setupTitle")}
        data-testid="ontology-build-setup-panel"
      >
        <DbObjectPanelHeader
          icon={UploadCloud}
          title={t("profiles.ontologyBuild.setupTitle")}
          description={t("profiles.ontologyBuild.setupHint")}
        />
        <Banner severity="info">{t("profiles.ontologyBuild.longRunningHint")}</Banner>
        <label className="grid grid-rows-[auto_1fr] gap-1 text-sm font-medium text-foreground">
          <span>{t("profiles.ontologyBuild.businessText")}</span>
          <textarea
            className={textareaClass}
            value={businessText}
            rows={4}
            placeholder={t("profiles.ontologyBuild.businessTextPlaceholder")}
            onChange={(event) => setBusinessText(event.currentTarget.value)}
          />
        </label>
        <div className="grid min-w-0 content-start gap-3">
          <div
            className="grid min-w-0 gap-2"
            data-testid="ontology-build-source-panel"
          >
            <FileDropzone
              label={t("profiles.ontologyBuild.sourceFiles")}
              accept={ONTOLOGY_SOURCE_FILE_FORMATS.accept}
              formatLabel={ONTOLOGY_SOURCE_FILE_FORMATS.formatLabel}
              multiple
              selectedCount={sourceFiles.length}
              hint={t("profiles.ontologyBuild.sourceFilesHint", {
                count: ONTOLOGY_SOURCE_FILE_MAX_COUNT,
              })}
              errorText={sourceFilesError}
              icon="file"
              dataTestId="ontology-build-source-files"
              onFiles={handleSourceFiles}
              onClear={() => {
                setSourceFiles([]);
                setSourceFilesError("");
              }}
            />
            {sourceFiles.length > 0 ? (
              <ul className="grid gap-1" aria-label={t("profiles.ontologyBuild.sourceFilesList")}>
                {sourceFiles.map((file) => (
                  <li
                    key={`${file.name}:${file.size}:${file.lastModified}`}
                    className="flex min-w-0 items-center gap-2 rounded-md bg-card px-3 py-1.5 text-sm"
                  >
                    <span className="min-w-0 flex-1 truncate text-foreground">{file.name}</span>
                    <span className="shrink-0 text-xs tabular-nums text-muted">
                      {Math.max(1, Math.ceil(file.size / 1024))} KB
                    </span>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      aria-label={t("profiles.ontologyBuild.sourceFileRemove", {
                        name: file.name,
                      })}
                      onClick={() => {
                        setSourceFilesError("");
                        setSourceFiles((current) => current.filter((item) => item !== file));
                      }}
                    >
                      <X size={15} aria-hidden="true" />
                    </Button>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
          <FileDropzone
            label={t("profiles.ontologyBuild.qaFile")}
            accept={ONTOLOGY_QA_FILE_FORMATS.accept}
            icon="spreadsheet"
            selectedText={
              qaFile ? t("profiles.ontologyBuild.qaFileSelected", { name: qaFile.name }) : undefined
            }
            formatLabel={ONTOLOGY_QA_FILE_FORMATS.formatLabel}
            hint={t("profiles.ontologyBuild.qaFileEmpty")}
            dataTestId="ontology-build-qa-file"
            onFiles={([file]) => setQaFile(file)}
            onClear={() => setQaFile(null)}
          />
          <SavedSourceDocumentsList
            documents={savedSourceDocuments}
            loading={savedSourceDocumentsLoading}
          />
        </div>

        <div className="flex flex-wrap items-center gap-3 border-t border-border pt-4">
          <Button
            type="button"
            variant="primary"
            size="lg"
            className="w-full sm:w-auto"
            loading={busy === "start" || jobRunning}
            disabled={busy === "start" || jobRunning}
            onClick={() => void startBuild()}
          >
            <UploadCloud size={15} aria-hidden="true" />
            <span>
              {jobRunning
                ? t("profiles.ontologyBuild.running")
                : t("profiles.ontologyBuild.run")}
            </span>
          </Button>
        </div>
      </section>

      <section
        className="grid w-full min-w-0 content-start gap-4 rounded-md border border-border bg-background p-3"
        aria-label={t("profiles.ontologyBuild.reviewTitle")}
        data-testid="ontology-build-review-panel"
      >
        <DbObjectPanelHeader
          icon={FileText}
          title={t("profiles.ontologyBuild.reviewTitle")}
          description={t("profiles.ontologyBuild.reviewHint")}
        />
      {!job && busy === "start" ? (
        <TimedLoadingState
          label={t("profiles.ontologyBuild.submitting")}
          operationKey="ontology-build-submit"
          placement="action"
          testId="ontology-build-submitting"
          activityIcon="none"
        />
      ) : null}

      {job ? (
        <WorkflowProgressStrip
          active={jobRunning}
          operationKey={job.id}
          startedAt={job.started_at ?? job.created_at}
          finishedAt={job.finished_at}
          title={t("profiles.ontologyBuild.progress.title")}
          titleId="ontology-build-progress-title"
          message={buildProgressMessage(job.status)}
          statusLabel={t(`profiles.ontologyBuild.jobStatus.${job.status}`)}
          statusVariant={buildStatusVariant(job.status)}
          tone={buildProgressTone(job.status)}
          stepsAriaLabel={t("profiles.ontologyBuild.progress.stepsLabel")}
          testId="ontology-build-steps"
          dataJobStatus={job.status}
          role={jobRunning ? "status" : undefined}
          collapsible={{
            collapsed: progressCollapsed,
            onCollapsedChange: setProgressCollapsed,
            collapseLabel: t("profiles.ontologyBuild.progress.collapse"),
            expandLabel: t("profiles.ontologyBuild.progress.expand"),
            toggleTestId: "ontology-build-progress-toggle",
          }}
          headerExtra={
            <span className="text-xs tabular-nums text-muted" data-testid="ontology-build-step-progress">
              {t("profiles.ontologyBuild.stepProgress", {
                done: job.steps.filter((step) =>
                  ["succeeded", "skipped", "failed"].includes(
                    effectiveBuildStepStatus(job.status, step.status)
                  )
                ).length,
                total: job.steps.length,
              })}
            </span>
          }
          meta={
            <span className="font-mono">
              {t("nl2sql.status.jobId", { id: `${job.id.slice(0, 8)}...` })}
            </span>
          }
          actions={
            jobRunning ? (
              <Button
                type="button"
                variant="danger"
                size="sm"
                loading={busy === "cancel"}
                disabled={busy === "cancel"}
                onClick={() => void cancelBuild()}
                data-testid="ontology-build-cancel"
              >
                <Ban size={14} aria-hidden="true" />
                <span>{t("profiles.ontologyBuild.cancel")}</span>
              </Button>
            ) : null
          }
          steps={job.steps.map((step, stepIndex) => {
            const displayStatus = effectiveBuildStepStatus(job.status, step.status);
            const displayFinishedAt =
              displayStatus !== step.status && job.finished_at ? job.finished_at : step.finished_at;
            const elapsed = formatElapsed(step.started_at, displayFinishedAt, nowTick);
            const stepEvents = groupedEvents?.get(stepIndex) ?? [];
            return {
              id: step.name,
              label: t(`profiles.ontologyBuild.step.${step.name}`),
              description: t(`profiles.ontologyBuild.stepDescription.${step.name}`),
              status: normalizeBuildStepStatus(displayStatus),
              statusLabel: t(`profiles.ontologyBuild.stepStatus.${displayStatus}`),
              elapsedLabel: elapsed,
              open:
                displayStatus === "running" ||
                displayStatus === "failed" ||
                Boolean(step.detail_ja) ||
                stepEvents.length > 0,
              testId: `ontology-build-step-${step.name}`,
              dataStatus: displayStatus,
              content: (
                <>
                  {step.detail_ja ? (
                    <p className="mt-2 border-l border-border pl-3 text-xs leading-5 text-muted">
                      {step.detail_ja}
                    </p>
                  ) : null}
                  {stepEvents.length > 0 ? (
                    <ol
                      className="mt-2 grid gap-1 border-l border-border pl-3"
                      aria-label={t("profiles.ontologyBuild.stepEventsLabel")}
                    >
                      <BuildEventRows events={stepEvents} />
                    </ol>
                  ) : null}
                </>
              ),
            };
          })}
          footer={
            schemaScopeFailure ||
            unscopedBuildError ||
            markdownDraftStale ||
            job.warnings_ja.length > 0 ||
            (job.sources?.length ?? 0) > 0 ? (
              <div className="mx-4 mb-4 grid gap-2">
                {schemaScopeFailure ? (
                  <Banner
                    severity="warning"
                    title={t("profiles.ontologyBuild.schemaRecoveryTitle")}
                    action={
                      onRefreshSchema ? (
                        <Button
                          type="button"
                          variant="secondary"
                          size="sm"
                          className="w-full sm:w-auto"
                          loading={refreshingSchema}
                          disabled={refreshingSchema}
                          onClick={() => void onRefreshSchema()}
                          data-testid="ontology-build-schema-refresh"
                        >
                          <RefreshCw size={15} aria-hidden="true" />
                          <span>{t("profiles.schemaRefresh.action")}</span>
                        </Button>
                      ) : undefined
                    }
                  >
                    {t("profiles.ontologyBuild.schemaRecoveryHint")}
                  </Banner>
                ) : null}
                {unscopedBuildError ? (
                  <Banner
                    severity="danger"
                    action={
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        className="w-full sm:w-auto"
                        loading={busy === "retry"}
                        disabled={busy !== "" && busy !== "retry"}
                        onClick={() => void retryBuild()}
                        data-testid="ontology-build-retry"
                      >
                        <RefreshCw size={15} aria-hidden="true" />
                        <span>{t("profiles.ontologyBuild.retryAction")}</span>
                      </Button>
                    }
                  >
                    {unscopedBuildError}
                  </Banner>
                ) : null}
                {markdownDraftStale ? (
                  <Banner severity="warning">
                    {t("profiles.ontologyBuild.markdownStaleWarning")}
                  </Banner>
                ) : null}
                {job.warnings_ja.length > 0 ? (
                  <details
                    open={job.status === "failed"}
                    className="group/disclosure rounded-md border border-warning/30 bg-warning-bg p-2 text-sm text-warning"
                  >
                    <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 font-semibold focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 [&::-webkit-details-marker]:hidden">
                      <span>
                        {t("profiles.ontologyBuild.warningsTitle")} ({job.warnings_ja.length})
                      </span>
                      <DisclosureChevron expanded="group" size={15} />
                    </summary>
                    <ul className="mt-2 grid gap-1 pl-4">
                      {job.warnings_ja.map((warning) => (
                        <li key={warning} className="list-disc">
                          {warning}
                        </li>
                      ))}
                    </ul>
                  </details>
                ) : null}
                {(job.sources?.length ?? 0) > 0 ? (
                  <ul className="grid gap-1" aria-label={t("profiles.ontologyBuild.sourceProgress")}>
                    {(job.sources ?? []).map((source) => (
                      <li
                        key={source.source_document_id}
                        className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-background px-3 py-2 text-sm"
                      >
                        <span className="min-w-0 break-all text-foreground">{source.filename}</span>
                        <span className="flex items-center gap-2">
                          <span className="text-xs tabular-nums text-muted">
                            {source.extracted_chunk_count ?? 0} chunks
                          </span>
                          <StatusBadge
                            variant={
                              source.status === "failed"
                                ? "danger"
                                : source.status === "extracted"
                                  ? "success"
                                  : "info"
                            }
                            label={t(`profiles.ontologyBuild.sourceStatus.${source.status}`)}
                          />
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ) : null
          }
        />
      ) : null}

      <section
        className="grid min-w-0 gap-3 rounded-md border border-border bg-background p-3"
        aria-label={t("profiles.ontologyBuild.markdownTitle")}
        data-testid="ontology-build-markdown"
      >
        <ContentActionBar
          ariaLabel={t("profiles.ontologyBuild.markdownActions")}
          title={
            <span className="flex min-w-0 items-center gap-2">
              <FileText size={16} className="shrink-0 text-primary" aria-hidden="true" />
              <span>{t("profiles.ontologyBuild.markdownTitle")}</span>
            </span>
          }
          description={t("profiles.ontologyBuild.markdownHint")}
          testId="ontology-build-markdown-actions"
        >
          <Button
            type="button"
            variant="secondary"
            size="sm"
            aria-label={t("profiles.ontologyBuild.markdownCopy")}
            disabled={!activeMarkdown.trim()}
            onClick={() => void copyMarkdownOutput()}
          >
            <ClipboardCopy size={15} aria-hidden="true" />
            <span>{t("profiles.ontologyBuild.markdownCopy")}</span>
          </Button>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            loading={busy === "save-draft"}
            disabled={
              activeMarkdownTab !== "draft" ||
              !hasDraftRevision ||
              !draftDirty ||
              publishRunning ||
              (busy !== "" && busy !== "save-draft")
            }
            onClick={() => void saveDraftMarkdown()}
          >
            <Save size={15} aria-hidden="true" />
            <span>{t("profiles.ontologyBuild.markdownSave")}</span>
          </Button>
        </ContentActionBar>

        <ManagementTabs
          activeView={activeMarkdownTab}
          tabs={markdownTabs}
          idPrefix="ontology-markdown"
          ariaLabel={t("profiles.ontologyBuild.markdownTabsLabel")}
          onViewChange={setActiveMarkdownTab}
        />

        {activeMarkdownTab === "published" && publishedAtMeta ? (
          <div
            className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted"
            role="status"
            aria-live="polite"
            data-testid="ontology-markdown-published-meta"
          >
            <span className="tabular-nums">{publishedAtMeta}</span>
          </div>
        ) : null}

        {markdownError ? (
          <Banner
            severity="danger"
            action={
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => void refreshMarkdown(profileId)}
              >
                <RefreshCw size={15} aria-hidden="true" />
                <span>{t("common.action.retry")}</span>
              </Button>
            }
          >
            {markdownError}
          </Banner>
        ) : null}

        {markdownLoading ? (
          <DbManagementLoadingSkeleton
            idPrefix="ontology-markdown"
            ariaLabel={t("profiles.ontologyBuild.markdownLoading")}
            variant="detail"
            operationKey={`ontology-markdown-load:${profileId ?? ""}`}
            onCancel={cancelMarkdownLoad}
            placement="panel"
            testId="ontology-markdown-loading"
          />
        ) : activeMarkdownTab === "draft" ? (
          <div
            id="ontology-markdown-panel-draft"
            role="tabpanel"
            aria-labelledby="ontology-markdown-tab-draft"
            className="grid min-w-0 gap-2"
          >
            {!hasDraftRevision && !draftMarkdown.trim() ? (
              // Draft 未生成時は巨大なコードエディタ枠を出さず、簡潔な空状態にする
              <div
                data-testid="ontology-markdown-draft-empty"
                className="grid min-h-28 place-items-center rounded-md border border-dashed border-border bg-muted/20 px-4 py-6"
              >
                <p className="text-sm leading-6 text-muted">
                  {t("profiles.ontologyBuild.markdownDraftPlaceholder")}
                </p>
              </div>
            ) : (
              <>
                <label className="sr-only" htmlFor="ontology-markdown-draft-editor">
                  {t("profiles.ontologyBuild.markdownTabAria.draft")}
                </label>
                <textarea
                  id="ontology-markdown-draft-editor"
                  data-testid="ontology-markdown-draft-editor"
                  className={markdownTextareaClass}
                  value={draftMarkdown}
                  rows={18}
                  spellCheck={false}
                  placeholder={t("profiles.ontologyBuild.markdownDraftPlaceholder")}
                  disabled={!hasDraftRevision || busy === "save-draft"}
                  onChange={(event) => {
                    const nextDraftMarkdown = event.currentTarget.value;
                    draftMarkdownRef.current = nextDraftMarkdown;
                    draftDirtyRef.current = true;
                    setDraftMarkdown(nextDraftMarkdown);
                    setDraftDirty(true);
                  }}
                />
              </>
            )}
          </div>
        ) : (
          <div
            id="ontology-markdown-panel-published"
            role="tabpanel"
            aria-labelledby="ontology-markdown-tab-published"
            className="grid min-w-0 gap-2"
          >
            {publishedMarkdown.trim() ? (
              <div
                data-testid="ontology-markdown-published-viewer"
                aria-label={t("profiles.ontologyBuild.markdownTabAria.published")}
                className="max-h-[32rem] min-h-[22rem] max-w-full overflow-auto rounded-md border border-border bg-code p-3 font-mono text-xs leading-6 text-code-fg"
              >
                <pre className="whitespace-pre-wrap break-words">
                  <code>{publishedMarkdown}</code>
                </pre>
              </div>
            ) : (
              // 公開前は巨大なコードビューア枠を出さず、簡潔な空状態にする
              <div
                data-testid="ontology-markdown-published-viewer"
                aria-label={t("profiles.ontologyBuild.markdownTabAria.published")}
                className="grid min-h-28 place-items-center rounded-md border border-dashed border-border bg-muted/20 px-4 py-6"
              >
                <p className="text-sm leading-6 text-muted">
                  {t("profiles.ontologyBuild.markdownPublishedEmpty")}
                </p>
              </div>
            )}
          </div>
        )}

        {!markdownLoading ? (
          <div
            className="flex flex-wrap items-center gap-3 border-t border-border pt-4"
            data-testid="ontology-publish-actions"
          >
            <Button
              type="button"
              variant="primary"
              size="lg"
              className="w-full sm:w-auto"
              loading={busy === "publish"}
              disabled={
                !hasDraftRevision ||
                publishRunning ||
                (busy !== "" && busy !== "publish")
              }
              onClick={() => void publish()}
            >
              <Sparkles size={15} aria-hidden="true" />
              <span>{t("profiles.ontologyBuild.publish")}</span>
            </Button>
            {draftDirty ? (
              <StatusBadge variant="warning" label={t("profiles.ontologyBuild.markdownUnsaved")} />
            ) : null}
          </div>
        ) : null}
        {!markdownLoading && publishJob ? (
          <div
            className="grid gap-2 rounded-md border border-border bg-background p-3"
            role="status"
            aria-live="polite"
            data-testid="ontology-publish-status"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-sm font-semibold text-foreground">
                {t("profiles.ontologyBuild.publishProgress")}
              </span>
              <StatusBadge
                variant={
                  publishJob.status === "failed"
                    ? "danger"
                    : publishJob.status === "succeeded"
                      ? "success"
                      : "info"
                }
                label={t(`profiles.ontologyBuild.publishStatus.${publishJob.status}`)}
              />
            </div>
            {publishJob.rdf_graph_name ? (
              <code className="break-all text-xs text-muted">
                {publishJob.rdf_graph_name} / {publishJob.inferred_graph_name}
              </code>
            ) : null}
          </div>
        ) : null}
      </section>
      </section>
    </section>
  );
}

function SectionHeading() {
  return (
    <DbObjectPanelHeader
      icon={Sparkles}
      title={t("profiles.ontologyBuild.title")}
      description={t("profiles.ontologyBuild.hint")}
    />
  );
}
