import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Ban,
  Check,
  Loader2,
  RotateCcw,
  Sparkles,
  UploadCloud,
  X,
} from "lucide-react";

import { Banner, Button, StatusBadge, toast } from "@engchina/production-ready-ui";

import { FixedSplitPane } from "@/components/layout/FixedSplitPane";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { PageNotice, usePageNotice } from "@/components/page-notice";
import { ErrorState } from "@/components/StateViews";
import { isAbortError } from "@/lib/api";
import { t } from "@/lib/i18n";
import { FileInputControl } from "../components/DbAdminShared";
import {
  DbManagementLoadingSkeleton,
  DbObjectPanelHeader,
} from "../components/DbObjectManagementShared";
import {
  acceptOntologyProposal,
  acceptOntologyProposalsBatch,
  ApiError,
  cancelOntologyBuildJob,
  getOntologyBuildJob,
  getOntologyPublishJob,
  listOntologyBuildJobs,
  listOntologyRevisions,
  listProfileOntologyProposals,
  publishOntologyRevision,
  rejectOntologyProposal,
  retryOntologyBuildJob,
  startOntologyBuild,
} from "./api";
import type {
  OntologyBuildJob,
  OntologyBuildStep,
  OntologyProposal,
  OntologyPublishJob,
  OntologyRevision,
} from "./types";

const POLL_INTERVAL_MS = 1000;
// 状態取得が連続で失敗したらポーリングを止めてエラー表示する(404 は即終端)
const MAX_POLL_FAILURES = 5;
const textareaClass =
  "min-h-24 w-full resize-y rounded-md border border-border bg-card px-3 py-2 text-sm leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40";

function stepStatusVariant(status: OntologyBuildStep["status"]) {
  if (status === "succeeded") return "success" as const;
  if (status === "failed") return "danger" as const;
  if (status === "running") return "info" as const;
  return "neutral" as const;
}

function formatElapsed(startIso: string | null | undefined, endIso: string | null | undefined, now: number): string {
  if (!startIso) return "";
  const start = Date.parse(startIso);
  if (Number.isNaN(start)) return "";
  const end = endIso ? Date.parse(endIso) : now;
  const seconds = Math.max(0, Math.floor((end - start) / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

function formatEventTime(iso: string): string {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime())
    ? ""
    : parsed.toLocaleTimeString("ja-JP", { hour12: false });
}

function proposalStatusVariant(status: OntologyProposal["status"]) {
  if (status === "accepted") return "success" as const;
  if (status === "rejected") return "danger" as const;
  return "info" as const;
}

export interface OntologyBuildSectionProps {
  profileId: string | null;
  onPublished?: () => void;
  /** 外部(テンプレート適用/RDF import)で提案が増えたときに増分して提案一覧を再読込する。 */
  proposalsRefreshToken?: number;
}

export function OntologyBuildSection({
  profileId,
  onPublished,
  proposalsRefreshToken,
}: OntologyBuildSectionProps) {
  const [businessText, setBusinessText] = useState("");
  const [qaFile, setQaFile] = useState<File | null>(null);
  const [sourceFiles, setSourceFiles] = useState<File[]>([]);
  const [runNaming, setRunNaming] = useState(true);
  const [runQa, setRunQa] = useState(true);
  const [runText, setRunText] = useState(true);
  const [job, setJob] = useState<OntologyBuildJob | null>(null);
  const [jobHistory, setJobHistory] = useState<OntologyBuildJob[]>([]);
  const [proposals, setProposals] = useState<OntologyProposal[]>([]);
  const [proposalsLoading, setProposalsLoading] = useState(false);
  const [proposalsError, setProposalsError] = useState("");
  const [draftRevision, setDraftRevision] = useState<OntologyRevision | null>(null);
  const [publishJob, setPublishJob] = useState<OntologyPublishJob | null>(null);
  const { notice, showNotice, clearNotice } = usePageNotice();
  const confirm = useConfirm();
  const [busy, setBusy] = useState("");
  // 承認/却下は行単位の busy(他の行の操作をブロックしない)
  const [reviewBusyIds, setReviewBusyIds] = useState<ReadonlySet<string>>(new Set());
  // 実行中ステップの経過秒を更新するための現在時刻(ポーリングと同じ周期で更新)
  const [nowTick, setNowTick] = useState(() => Date.now());
  const timelineRef = useRef<HTMLOListElement | null>(null);
  // プロファイル切替後に in-flight 応答が旧プロファイルの状態を上書きしないためのガード
  const profileIdRef = useRef(profileId);
  const proposalsRequestIdRef = useRef(0);
  const pollInFlightRef = useRef(false);
  const pollFailureCountRef = useRef(0);
  // 終端(完了/失敗)通知を job ごとに一度だけ出す
  const terminalHandledRef = useRef<string | null>(null);

  const jobRunning = job !== null && (job.status === "queued" || job.status === "running");
  const publishRunning =
    publishJob !== null &&
    ["queued", "materializing", "validating"].includes(publishJob.status);
  const jobId = job?.id ?? null;

  // 最新の構築実行(session)分の提案だけをレビュー対象にする。
  // 過去 run に残った submitted 提案を「すべて承認」へ混ぜない(混在防止)。
  // created_at は ISO8601 なので辞書順比較で時系列順になる(Date 変換不要・決定論)。
  const latestSessionId = useMemo(() => {
    let latest: OntologyProposal | null = null;
    for (const p of proposals) {
      if (!latest || (p.created_at ?? "") > (latest.created_at ?? "")) latest = p;
    }
    return latest?.session_id ?? null;
  }, [proposals]);

  const runProposals = useMemo(
    () =>
      latestSessionId ? proposals.filter((p) => p.session_id === latestSessionId) : proposals,
    [proposals, latestSessionId]
  );

  const refreshProposals = useCallback(async (targetProfileId: string, signal?: AbortSignal) => {
    const requestId = proposalsRequestIdRef.current + 1;
    proposalsRequestIdRef.current = requestId;
    setProposalsLoading(true);
    setProposalsError("");
    try {
      const next = await listProfileOntologyProposals(targetProfileId, { signal });
      if (
        profileIdRef.current === targetProfileId &&
        proposalsRequestIdRef.current === requestId
      ) {
        setProposals(next);
      }
    } catch (err) {
      if (isAbortError(err)) return;
      if (
        profileIdRef.current === targetProfileId &&
        proposalsRequestIdRef.current === requestId
      ) {
        setProposalsError(t("profiles.ontologyBuild.proposalsLoadError"));
      }
    } finally {
      if (
        profileIdRef.current === targetProfileId &&
        proposalsRequestIdRef.current === requestId
      ) {
        setProposalsLoading(false);
      }
    }
  }, []);

  // 承認済みで未公開の draft をリロード後も検出し、「公開」導線を維持する。
  const detectPendingDraft = useCallback(async (signal?: AbortSignal) => {
    try {
      const data = await listOntologyRevisions({ signal });
      const active = data.revisions.find(
        (revision) => revision.id === data.active_revision_id
      );
      if (!active) return;
      const pending = data.revisions
        .filter(
          (revision) =>
            revision.status === "draft" &&
            revision.schema_fingerprint === active.schema_fingerprint &&
            revision.version > active.version
        )
        .sort((left, right) => right.version - left.version)[0];
      setDraftRevision(pending ?? null);
    } catch (err) {
      if (isAbortError(err)) return;
      // 取得できない場合は accept 応答由来の draft 表示にフォールバック
    }
  }, []);

  useEffect(() => {
    profileIdRef.current = profileId;
    setJob(null);
    setJobHistory([]);
    setProposals([]);
    setDraftRevision(null);
    setPublishJob(null);
    clearNotice();
    setProposalsLoading(Boolean(profileId));
    setProposalsError("");
    setBusinessText("");
    setQaFile(null);
    setSourceFiles([]);
    const controller = new AbortController();
    if (profileId) {
      void refreshProposals(profileId, controller.signal);
      void detectPendingDraft(controller.signal);
      // リロード/プロファイル切替後も直近 job を復元する(実行中なら進捗追跡を再開)
      listOntologyBuildJobs(profileId, 5, { signal: controller.signal })
        .then((jobs) => {
          if (controller.signal.aborted) return;
          setJobHistory(jobs);
          const latest = jobs[0];
          if (!latest) return;
          if (latest.status === "queued" || latest.status === "running") {
            setJob(latest);
          } else if (latest.status === "failed" || latest.status === "cancelled") {
            // 終端カード(+再実行ボタン)を復元するが、完了トーストは再通知しない
            terminalHandledRef.current = latest.id;
            setJob(latest);
          }
        })
        .catch(() => {
          // 履歴は補助情報のため取得失敗で画面を止めない(新規実行は可能)
        });
    }
    return () => controller.abort();
  }, [detectPendingDraft, profileId, refreshProposals]);

  // 外部トリガー(テンプレート適用/RDF import)による提案一覧のみの再読込(フォームは保持)。
  useEffect(() => {
    if (!profileId || !proposalsRefreshToken) return;
    const controller = new AbortController();
    void refreshProposals(profileId, controller.signal);
    return () => controller.abort();
  }, [proposalsRefreshToken, profileId, refreshProposals]);

  // job ポーリング(1s)。完了で停止し、提案一覧を更新する。
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
            next.status === "failed" ||
            next.status === "cancelled";
          if (terminal && terminalHandledRef.current !== jobId) {
            terminalHandledRef.current = jobId;
            void refreshProposals(profileId);
            void listOntologyBuildJobs(profileId, 5)
              .then(setJobHistory)
              .catch(() => {});
            if (next.status === "succeeded") {
              const count = next.proposal_ids.length;
              toast.success(
                count === 0
                  ? t("profiles.ontologyBuild.jobSucceededEmpty")
                  : t("profiles.ontologyBuild.jobSucceeded", { count })
              );
            } else if (next.status === "cancelled") {
              showNotice("info", t("profiles.ontologyBuild.cancelled"));
            } else if (next.error_message_ja) {
              showNotice(
                "danger",
                `${next.error_message_ja} ${t("profiles.ontologyBuild.error.retryHint")}`
              );
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
            // 提案は永続化済みなので job が消えても一覧は取得できる
            void refreshProposals(profileId);
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
  }, [jobRunning, jobId, profileId, refreshProposals, showNotice]);

  useEffect(() => {
    if (!publishRunning || !publishJob) return;
    let cancelled = false;
    let currentController: AbortController | null = null;
    const timer = window.setInterval(() => {
      currentController = new AbortController();
      void getOntologyPublishJob(publishJob.id, { signal: currentController.signal })
        .then((next) => {
          if (cancelled) return;
          setPublishJob(next);
          if (next.status === "succeeded") {
            setDraftRevision(null);
            toast.success(t("profiles.ontologyBuild.published"));
            onPublished?.();
          } else if (next.status === "failed") {
            showNotice(
              "danger",
              next.error_message_ja || t("profiles.ontologyBuild.error.publish")
            );
          }
        })
        .catch((err) => {
          if (cancelled || isAbortError(err)) return;
          if (!cancelled) {
            showNotice(
              "danger",
              err instanceof Error ? err.message : t("profiles.ontologyBuild.error.publish")
            );
            setPublishJob(null);
          }
        })
        .finally(() => {
          currentController = null;
        });
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      currentController?.abort();
    };
  }, [onPublished, publishJob, publishRunning, showNotice]);

  // アクティビティタイムラインは新着イベントで末尾へ自動スクロールする
  const eventCount = job?.events?.length ?? 0;
  useEffect(() => {
    const list = timelineRef.current;
    if (list) list.scrollTop = list.scrollHeight;
  }, [eventCount]);

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
    setBusy("start");
    clearNotice();
    try {
      const started = await startOntologyBuild(targetProfileId, {
        businessText,
        qaFile,
        sourceFiles,
        runSchemaNaming: runNaming,
        runQaExtraction: runQa,
        runTextExtraction: runText,
      });
      // プロファイル切替後に旧プロファイルの job を表示しない
      if (profileIdRef.current === targetProfileId) setJob(started);
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
      if (profileId) {
        void listOntologyBuildJobs(profileId, 5)
          .then(setJobHistory)
          .catch(() => {});
      }
    } catch (err) {
      showNotice(
        "danger",
        err instanceof Error ? err.message : t("profiles.ontologyBuild.error.cancel")
      );
    } finally {
      setBusy("");
    }
  };

  const retryBuild = async () => {
    const failedJob = job;
    if (!failedJob) return;
    setBusy("retry");
    clearNotice();
    try {
      const next = await retryOntologyBuildJob(failedJob.id);
      terminalHandledRef.current = null;
      setJob(next);
      toast.success(t("profiles.ontologyBuild.retryStarted"));
    } catch (err) {
      showNotice(
        "danger",
        err instanceof Error ? err.message : t("profiles.ontologyBuild.error.retry")
      );
    } finally {
      setBusy("");
    }
  };

  const review = async (proposal: OntologyProposal, action: "accept" | "reject") => {
    setReviewBusyIds((current) => new Set([...current, proposal.id]));
    clearNotice();
    try {
      const result =
        action === "accept"
          ? await acceptOntologyProposal(proposal.id)
          : await rejectOntologyProposal(proposal.id);
      if (action === "accept" && result.draft?.revision) {
        setDraftRevision(result.draft.revision);
      }
      // 応答の proposal でその行だけを楽観的に更新(全件再取得を待たない)
      setProposals((current) =>
        current.map((item) => (item.id === proposal.id ? result.proposal : item))
      );
    } catch (err) {
      showNotice("danger", err instanceof Error ? err.message : t("profiles.ontologyBuild.error.review"));
    } finally {
      setReviewBusyIds((current) => {
        const next = new Set(current);
        next.delete(proposal.id);
        return next;
      });
    }
  };

  const acceptAll = async (pending: OntologyProposal[]) => {
    const ids = pending.map((proposal) => proposal.id);
    setBusy("accept-all");
    clearNotice();
    try {
      const result = await acceptOntologyProposalsBatch(ids);
      if (result.draft?.revision) setDraftRevision(result.draft.revision);
      const byId = new Map(result.proposals.map((proposal) => [proposal.id, proposal]));
      setProposals((current) => current.map((item) => byId.get(item.id) ?? item));
    } catch (err) {
      showNotice("danger", err instanceof Error ? err.message : t("profiles.ontologyBuild.error.review"));
    } finally {
      setBusy("");
    }
  };

  const publish = async () => {
    if (!draftRevision) return;
    setBusy("publish");
    clearNotice();
    try {
      setPublishJob(await publishOntologyRevision(draftRevision.id, draftRevision.etag));
    } catch (err) {
      showNotice("danger", err instanceof Error ? err.message : t("profiles.ontologyBuild.error.publish"));
    } finally {
      setBusy("");
    }
  };

  const toggles: Array<{
    checked: boolean;
    onChange: (value: boolean) => void;
    label: string;
  }> = [
    { checked: runNaming, onChange: setRunNaming, label: t("profiles.ontologyBuild.targetNaming") },
    { checked: runQa, onChange: setRunQa, label: t("profiles.ontologyBuild.targetQa") },
    { checked: runText, onChange: setRunText, label: t("profiles.ontologyBuild.targetText") },
  ];

  return (
    <section
      className="grid min-w-0 gap-4 rounded-md border border-border bg-card p-4 shadow-sm"
      aria-label={t("profiles.ontologyBuild.title")}
      data-testid="profile-ontology-build"
    >
      <SectionHeading />
      <PageNotice notice={notice} />
      <FixedSplitPane
        splitId="ontology-build-workspace"
        preferredWidePane="right"
        minLeftPaneWidthPx={420}
        minRightPaneWidthPx={520}
        left={
          <section
            className="grid min-w-0 content-start gap-4 rounded-md border border-border bg-background p-3"
            aria-label={t("profiles.ontologyBuild.setupTitle")}
          >
            <DbObjectPanelHeader
              icon={UploadCloud}
              title={t("profiles.ontologyBuild.setupTitle")}
              description={t("profiles.ontologyBuild.setupHint")}
            />
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
            className="grid gap-2 rounded-md border border-border bg-background p-3"
            data-testid="ontology-build-source-panel"
          >
            <label className="grid gap-1 text-sm font-medium text-foreground">
              <span>{t("profiles.ontologyBuild.sourceFiles")}</span>
              <span className="text-xs font-normal leading-5 text-muted">
                {t("profiles.ontologyBuild.sourceFilesHint")}
              </span>
              <input
                type="file"
                multiple
                accept=".pdf,.docx,.txt,.md,.csv,.tsv,.xlsx,.xlsm"
                className="min-h-11 rounded-md border border-border bg-card px-3 py-2 text-sm file:mr-3 file:rounded file:border-0 file:bg-primary/10 file:px-3 file:py-1 file:text-primary focus:outline-none focus:ring-2 focus:ring-ring/40"
                data-testid="ontology-build-source-files"
                onChange={(event) => {
                  const picked = Array.from(event.currentTarget.files ?? []);
                  setSourceFiles((current) => {
                    const byKey = new Map(
                      [...current, ...picked].map((file) => [
                        `${file.name}:${file.size}:${file.lastModified}`,
                        file,
                      ])
                    );
                    return [...byKey.values()];
                  });
                  event.currentTarget.value = "";
                }}
              />
            </label>
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
                      onClick={() =>
                        setSourceFiles((current) => current.filter((item) => item !== file))
                      }
                    >
                      <X size={15} aria-hidden="true" />
                    </Button>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
          <FileInputControl
            label={t("profiles.ontologyBuild.qaFile")}
            accept=".csv,.tsv,.xlsx,.xlsm"
            icon="spreadsheet"
            filename={qaFile?.name ?? ""}
            selectedText={
              qaFile ? t("profiles.ontologyBuild.qaFileSelected", { name: qaFile.name }) : undefined
            }
            emptyText={t("profiles.ontologyBuild.qaFileEmpty")}
            pickText={t("profiles.ontologyBuild.qaFilePick")}
            dataTestId="ontology-build-qa-file"
            onPick={(file) => setQaFile(file)}
            onClear={() => setQaFile(null)}
          />
          <fieldset className="grid gap-2">
            <legend className="text-sm font-medium text-foreground">
              {t("profiles.ontologyBuild.targets")}
            </legend>
            {toggles.map((toggle) => (
              <label
                key={toggle.label}
                className="flex min-h-11 cursor-pointer items-center gap-3 rounded-md border border-border px-3 py-2 text-sm text-foreground focus-within:ring-2 focus-within:ring-ring/40"
              >
                <input
                  type="checkbox"
                  className="h-5 w-5 shrink-0 accent-primary"
                  checked={toggle.checked}
                  onChange={(event) => toggle.onChange(event.currentTarget.checked)}
                />
                <span>{toggle.label}</span>
              </label>
            ))}
          </fieldset>
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
        }
        right={
          <section
            className="grid min-w-0 content-start gap-4 rounded-md border border-border bg-background p-3"
            aria-label={t("profiles.ontologyBuild.reviewTitle")}
          >
            <DbObjectPanelHeader
              icon={Check}
              title={t("profiles.ontologyBuild.reviewTitle")}
              description={t("profiles.ontologyBuild.reviewHint")}
            />
      {!job && busy === "start" ? (
        // スピナーは「AI 構築を実行」ボタン内の loading 表示に一本化する
        // (ここに Loader2 を置くと二重スピナーになるため、テキストのみ表示)。
        <div
          className="rounded-md border border-border bg-background p-3 text-sm text-foreground"
          role="status"
          data-testid="ontology-build-submitting"
        >
          {t("profiles.ontologyBuild.submitting")}
        </div>
      ) : null}

      {job ? (
        <section
          className="grid gap-2 rounded-md border border-border bg-background p-3"
          aria-label={t("profiles.ontologyBuild.stepsTitle")}
          aria-live="polite"
          data-testid="ontology-build-steps"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <h4 className="text-sm font-semibold text-foreground">
                {t("profiles.ontologyBuild.stepsTitle")}
              </h4>
              <span className="text-xs tabular-nums text-muted" data-testid="ontology-build-step-progress">
                {t("profiles.ontologyBuild.stepProgress", {
                  done: job.steps.filter((step) =>
                    ["succeeded", "skipped", "failed"].includes(step.status)
                  ).length,
                  total: job.steps.length,
                })}
              </span>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {job.started_at ? (
                <span className="text-xs tabular-nums text-muted">
                  {t("profiles.ontologyBuild.elapsed", {
                    time: formatElapsed(job.started_at, job.finished_at, nowTick),
                  })}
                </span>
              ) : null}
              {jobRunning ? (
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
              ) : null}
              {job.status === "failed" || job.status === "cancelled" ? (
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  loading={busy === "retry"}
                  disabled={busy === "retry"}
                  onClick={() => void retryBuild()}
                  data-testid="ontology-build-retry"
                >
                  <RotateCcw size={14} aria-hidden="true" />
                  <span>{t("profiles.ontologyBuild.retry")}</span>
                </Button>
              ) : null}
            </div>
          </div>
          <ul className="grid gap-2">
            {job.steps.map((step) => {
              const elapsed = formatElapsed(step.started_at, step.finished_at, nowTick);
              return (
                <li
                  key={step.name}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-card px-3 py-2"
                >
                  <span className="flex min-w-0 items-center gap-2 text-sm text-foreground">
                    {step.status === "running" ? (
                      <Loader2
                        size={14}
                        className="shrink-0 animate-spin text-primary"
                        aria-hidden="true"
                      />
                    ) : null}
                    <span className="min-w-0">
                      {t(`profiles.ontologyBuild.step.${step.name}`)}
                      {step.detail_ja ? (
                        <span className="ml-2 text-xs text-muted">{step.detail_ja}</span>
                      ) : null}
                    </span>
                  </span>
                  <span className="flex shrink-0 items-center gap-2">
                    {elapsed ? (
                      <span className="text-xs tabular-nums text-muted">{elapsed}</span>
                    ) : null}
                    <StatusBadge
                      variant={stepStatusVariant(step.status)}
                      label={t(`profiles.ontologyBuild.stepStatus.${step.status}`)}
                    />
                  </span>
                </li>
              );
            })}
          </ul>
          {(job.events?.length ?? 0) > 0 ? (
            <div data-testid="ontology-build-timeline">
              <h5 className="text-xs font-semibold text-muted">
                {t("profiles.ontologyBuild.timelineTitle")}
              </h5>
              <ol
                ref={timelineRef}
                className="mt-1 grid max-h-40 gap-1 overflow-y-auto rounded-md border border-border bg-card p-2"
              >
                {(job.events ?? []).map((event, index) => (
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
              </ol>
            </div>
          ) : null}
          {job.warnings_ja.length > 0 ? (
            <details
              open={job.status === "failed"}
              className="rounded-md border border-warning/30 bg-warning-bg p-2 text-sm text-warning"
            >
              <summary className="cursor-pointer font-semibold">
                {t("profiles.ontologyBuild.warningsTitle")} ({job.warnings_ja.length})
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
                  className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm"
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
        </section>
      ) : null}

      {jobHistory.length > 0 ? (
        <details
          className="rounded-md border border-border bg-background p-2 text-sm"
          data-testid="ontology-build-history"
        >
          <summary className="cursor-pointer font-semibold text-foreground">
            {t("profiles.ontologyBuild.historyTitle")} ({jobHistory.length})
          </summary>
          <ul className="mt-2 grid gap-1">
            {jobHistory.map((item) => (
              <li
                key={item.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-card px-3 py-2"
              >
                <span className="min-w-0 text-xs text-foreground">
                  <span className="tabular-nums text-muted">
                    {item.started_at ? formatEventTime(item.started_at) : "—"}
                  </span>
                  {item.error_message_ja ? (
                    <span className="ml-2 break-words text-muted">
                      {item.error_message_ja}
                    </span>
                  ) : null}
                </span>
                <span className="flex shrink-0 items-center gap-2">
                  {item.started_at ? (
                    <span className="text-xs tabular-nums text-muted">
                      {formatElapsed(item.started_at, item.finished_at, nowTick)}
                    </span>
                  ) : null}
                  <StatusBadge
                    variant={
                      item.status === "succeeded"
                        ? "success"
                        : item.status === "failed"
                          ? "danger"
                          : item.status === "cancelled"
                            ? "neutral"
                            : "info"
                    }
                    label={t(`profiles.ontologyBuild.jobStatus.${item.status}`)}
                  />
                </span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      <section
        className="grid gap-2"
        aria-label={t("profiles.ontologyBuild.proposalsTitle")}
        data-testid="ontology-build-proposals"
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h4 className="text-sm font-semibold text-foreground">
              {t("profiles.ontologyBuild.proposalsTitle")}
            </h4>
            <p className="mt-1 text-sm text-muted">
              {t("profiles.ontologyBuild.proposalsHint")}
            </p>
          </div>
          {(() => {
            const pending = runProposals.filter((proposal) => proposal.status === "submitted");
            if (pending.length < 2) return null;
            return (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                loading={busy === "accept-all"}
                disabled={busy === "accept-all" || reviewBusyIds.size > 0}
                onClick={() => void acceptAll(pending)}
              >
                <Check size={15} aria-hidden="true" />
                <span>{t("profiles.ontologyBuild.acceptAll", { count: pending.length })}</span>
              </Button>
            );
          })()}
        </div>
        {proposalsLoading ? (
          <DbManagementLoadingSkeleton
            idPrefix="ontology-proposals"
            ariaLabel={t("profiles.ontologyBuild.proposalsLoading")}
            variant="list"
            rows={4}
          />
        ) : proposalsError ? (
          <ErrorState
            message={proposalsError}
            onRetry={() => void refreshProposals(profileId)}
          />
        ) : runProposals.length === 0 ? (
          <p className="rounded-md border border-dashed border-border bg-background p-4 text-sm text-muted">
            {t("profiles.ontologyBuild.proposalsEmpty")}
          </p>
        ) : (
          // 約 5 件分の高さで固定し、それ以上は内側スクロール(件数でセクションが伸びない)
          <ul className="grid max-h-96 gap-2 overflow-y-auto pr-1" data-testid="ontology-proposal-list">
            {runProposals.map((proposal) => {
              const title = proposal.title_ja || proposal.summary || proposal.id;
              const reviewed = proposal.status === "accepted" || proposal.status === "rejected";
              return (
                <li
                  key={proposal.id}
                  className="grid gap-2 rounded-md border border-border bg-card p-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center"
                >
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusBadge
                        variant="neutral"
                        label={t(`profiles.ontologyBuild.kind.${proposal.kind ?? "query_example"}`)}
                      />
                      <StatusBadge
                        variant={proposalStatusVariant(proposal.status)}
                        label={t(`profiles.ontologyBuild.status.${proposal.status}`)}
                      />
                      <span className="break-words text-sm font-semibold text-foreground">
                        {title}
                      </span>
                    </div>
                    {proposal.description_ja ? (
                      <p className="mt-1 break-words text-xs leading-5 text-muted">
                        {proposal.description_ja}
                      </p>
                    ) : null}
                  </div>
                  {!reviewed ? (
                    <div className="flex shrink-0 gap-2">
                      <Button
                        type="button"
                      variant="secondary"
                        size="sm"
                        loading={reviewBusyIds.has(proposal.id)}
                        disabled={reviewBusyIds.has(proposal.id) || busy === "accept-all"}
                        aria-label={t("profiles.ontologyBuild.acceptAria", { title })}
                        onClick={() => void review(proposal, "accept")}
                      >
                        <Check size={15} aria-hidden="true" />
                        <span>{t("profiles.ontologyBuild.accept")}</span>
                      </Button>
                      <Button
                        type="button"
                        variant="danger"
                        size="sm"
                        loading={reviewBusyIds.has(proposal.id)}
                        disabled={reviewBusyIds.has(proposal.id) || busy === "accept-all"}
                        aria-label={t("profiles.ontologyBuild.rejectAria", { title })}
                        onClick={() => void review(proposal, "reject")}
                      >
                        <X size={15} aria-hidden="true" />
                        <span>{t("profiles.ontologyBuild.reject")}</span>
                      </Button>
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
        {draftRevision ? (
          <div className="flex flex-wrap items-center gap-3 rounded-md border border-primary/30 bg-primary/10 p-3">
            <p className="min-w-0 flex-1 text-sm text-foreground">
              {t("profiles.ontologyBuild.publishHint", {
                version: String(draftRevision.version),
              })}
            </p>
            <Button
              type="button"
              variant="primary"
              size="lg"
              loading={busy === "publish"}
              disabled={publishRunning || (busy !== "" && busy !== "publish")}
              onClick={() => void publish()}
            >
              <Sparkles size={15} aria-hidden="true" />
              <span>{t("profiles.ontologyBuild.publish")}</span>
            </Button>
          </div>
        ) : null}
        {publishJob ? (
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
        }
      />
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
