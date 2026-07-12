import { useCallback, useEffect, useRef, useState } from "react";
import {
  Check,
  ClipboardCopy,
  FileCode2,
  Loader2,
  Sparkles,
  UploadCloud,
  X,
} from "lucide-react";

import { Banner, Button, StatusBadge } from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";
import { FileInputControl } from "../components/DbAdminShared";
import {
  acceptOntologyProposal,
  acceptOntologyProposalsBatch,
  fetchProfileOntologyMermaid,
  getOntologyBuildJob,
  listOntologyRevisions,
  listProfileOntologyProposals,
  publishOntologyRevision,
  rejectOntologyProposal,
  startOntologyBuild,
} from "./api";
import type {
  OntologyBuildJob,
  OntologyBuildStep,
  OntologyProposal,
  OntologyRevision,
} from "./types";

const POLL_INTERVAL_MS = 1000;
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
}

export function OntologyBuildSection({ profileId, onPublished }: OntologyBuildSectionProps) {
  const [businessText, setBusinessText] = useState("");
  const [qaFile, setQaFile] = useState<File | null>(null);
  const [runNaming, setRunNaming] = useState(true);
  const [runQa, setRunQa] = useState(true);
  const [runText, setRunText] = useState(true);
  const [job, setJob] = useState<OntologyBuildJob | null>(null);
  const [proposals, setProposals] = useState<OntologyProposal[]>([]);
  const [draftRevision, setDraftRevision] = useState<OntologyRevision | null>(null);
  const [message, setMessage] = useState<{ tone: "success" | "danger"; text: string } | null>(
    null
  );
  const [busy, setBusy] = useState("");
  // 承認/却下は行単位の busy(他の行の操作をブロックしない)
  const [reviewBusyIds, setReviewBusyIds] = useState<ReadonlySet<string>>(new Set());
  const [mermaid, setMermaid] = useState("");
  const [mermaidCopied, setMermaidCopied] = useState(false);
  // 実行中ステップの経過秒を更新するための現在時刻(ポーリングと同じ周期で更新)
  const [nowTick, setNowTick] = useState(() => Date.now());
  const timelineRef = useRef<HTMLOListElement | null>(null);

  const jobRunning = job !== null && (job.status === "queued" || job.status === "running");

  const refreshProposals = useCallback(async (targetProfileId: string) => {
    try {
      setProposals(await listProfileOntologyProposals(targetProfileId));
    } catch {
      // 一覧取得の失敗は致命的ではない(次の操作で再取得する)
    }
  }, []);

  // 承認済みで未公開の draft をリロード後も検出し、「公開」導線を維持する。
  const detectPendingDraft = useCallback(async () => {
    try {
      const data = await listOntologyRevisions();
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
    } catch {
      // 取得できない場合は accept 応答由来の draft 表示にフォールバック
    }
  }, []);

  useEffect(() => {
    setJob(null);
    setProposals([]);
    setDraftRevision(null);
    setMessage(null);
    setMermaid("");
    setBusinessText("");
    setQaFile(null);
    if (profileId) {
      void refreshProposals(profileId);
      void detectPendingDraft();
    }
  }, [detectPendingDraft, profileId, refreshProposals]);

  // job ポーリング(1s)。完了で停止し、提案一覧を更新する。
  useEffect(() => {
    if (!jobRunning || !job || !profileId) return;
    const timer = window.setInterval(() => {
      setNowTick(Date.now());
      void getOntologyBuildJob(job.id)
        .then((next) => {
          setJob(next);
          if (next.status === "succeeded" || next.status === "failed") {
            void refreshProposals(profileId);
            if (next.status === "succeeded") {
              setMessage({ tone: "success", text: t("profiles.ontologyBuild.jobSucceeded") });
            }
          }
        })
        .catch(() => {
          // 一時的な取得失敗は次のポーリングで回復する
        });
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [job, jobRunning, profileId, refreshProposals]);

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
    setBusy("start");
    setMessage(null);
    try {
      const started = await startOntologyBuild(profileId, {
        businessText,
        qaFile,
        runSchemaNaming: runNaming,
        runQaExtraction: runQa,
        runTextExtraction: runText,
      });
      setJob(started);
    } catch (err) {
      setMessage({
        tone: "danger",
        text: err instanceof Error ? err.message : t("profiles.ontologyBuild.error.start"),
      });
    } finally {
      setBusy("");
    }
  };

  const review = async (proposal: OntologyProposal, action: "accept" | "reject") => {
    setReviewBusyIds((current) => new Set([...current, proposal.id]));
    setMessage(null);
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
      setMessage({
        tone: "danger",
        text: err instanceof Error ? err.message : t("profiles.ontologyBuild.error.review"),
      });
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
    setReviewBusyIds(new Set(ids));
    setMessage(null);
    try {
      const result = await acceptOntologyProposalsBatch(ids);
      if (result.draft?.revision) setDraftRevision(result.draft.revision);
      const byId = new Map(result.proposals.map((proposal) => [proposal.id, proposal]));
      setProposals((current) => current.map((item) => byId.get(item.id) ?? item));
    } catch (err) {
      setMessage({
        tone: "danger",
        text: err instanceof Error ? err.message : t("profiles.ontologyBuild.error.review"),
      });
    } finally {
      setBusy("");
      setReviewBusyIds(new Set());
    }
  };

  const publish = async () => {
    if (!draftRevision) return;
    setBusy("publish");
    setMessage(null);
    try {
      await publishOntologyRevision(draftRevision.id, draftRevision.etag);
      setDraftRevision(null);
      setMessage({ tone: "success", text: t("profiles.ontologyBuild.published") });
      setMermaid("");
      onPublished?.();
    } catch (err) {
      setMessage({
        tone: "danger",
        text: err instanceof Error ? err.message : t("profiles.ontologyBuild.error.publish"),
      });
    } finally {
      setBusy("");
    }
  };

  const loadMermaid = async () => {
    setBusy("mermaid");
    try {
      const data = await fetchProfileOntologyMermaid(profileId);
      setMermaid(data.mermaid);
    } catch (err) {
      setMessage({
        tone: "danger",
        text: err instanceof Error ? err.message : t("profiles.ontologyBuild.error.mermaid"),
      });
    } finally {
      setBusy("");
    }
  };

  const copyMermaid = async () => {
    try {
      await navigator.clipboard.writeText(mermaid);
      setMermaidCopied(true);
      window.setTimeout(() => setMermaidCopied(false), 2000);
    } catch {
      // clipboard 不許可時は何もしない(表示テキストから手動コピー可能)
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
      className="grid gap-4 rounded-md border border-border bg-card p-3"
      aria-label={t("profiles.ontologyBuild.title")}
      data-testid="profile-ontology-build"
    >
      <SectionHeading />

      <div className="grid gap-3 lg:grid-cols-2">
        <label className="grid gap-1 text-sm font-medium text-foreground">
          <span>{t("profiles.ontologyBuild.businessText")}</span>
          <textarea
            className={textareaClass}
            value={businessText}
            rows={4}
            placeholder={t("profiles.ontologyBuild.businessTextPlaceholder")}
            onChange={(event) => setBusinessText(event.currentTarget.value)}
          />
        </label>
        <div className="grid content-start gap-3">
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
      </div>

      <div>
        <Button
          type="button"
          variant="primary"
          size="sm"
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

      {message ? (
        <Banner severity={message.tone === "success" ? "success" : "danger"}>
          {message.text}
        </Banner>
      ) : null}

      {!job && busy === "start" ? (
        <div
          className="flex items-center gap-2 rounded-md border border-border bg-background p-3 text-sm text-foreground"
          role="status"
          data-testid="ontology-build-submitting"
        >
          <Loader2 size={15} className="shrink-0 animate-spin text-primary" aria-hidden="true" />
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
            <h4 className="text-sm font-semibold text-foreground">
              {t("profiles.ontologyBuild.stepsTitle")}
            </h4>
            {job.started_at ? (
              <span className="text-xs tabular-nums text-muted">
                {t("profiles.ontologyBuild.elapsed", {
                  time: formatElapsed(job.started_at, job.finished_at, nowTick),
                })}
              </span>
            ) : null}
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
          {job.status === "failed" && job.error_message_ja ? (
            <Banner severity="danger" title={t("profiles.ontologyBuild.jobFailedTitle")}>
              {job.error_message_ja}
            </Banner>
          ) : null}
          {job.warnings_ja.length > 0 ? (
            <details className="rounded-md border border-warning/30 bg-warning-bg p-2 text-sm text-warning">
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
        </section>
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
            const pending = proposals.filter((proposal) => proposal.status === "submitted");
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
        {proposals.length === 0 ? (
          <p className="rounded-md border border-dashed border-border bg-background p-4 text-sm text-muted">
            {t("profiles.ontologyBuild.proposalsEmpty")}
          </p>
        ) : (
          // 約 5 件分の高さで固定し、それ以上は内側スクロール(件数でセクションが伸びない)
          <ul className="grid max-h-96 gap-2 overflow-y-auto pr-1" data-testid="ontology-proposal-list">
            {proposals.map((proposal) => {
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
                        variant="primary"
                        size="sm"
                        loading={reviewBusyIds.has(proposal.id)}
                        disabled={reviewBusyIds.has(proposal.id)}
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
                        disabled={reviewBusyIds.has(proposal.id)}
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
              size="sm"
              loading={busy === "publish"}
              disabled={busy !== "" && busy !== "publish"}
              onClick={() => void publish()}
            >
              <Sparkles size={15} aria-hidden="true" />
              <span>{t("profiles.ontologyBuild.publish")}</span>
            </Button>
          </div>
        ) : null}
      </section>

      <details className="rounded-md border border-border bg-background p-3">
        <summary className="flex min-h-11 cursor-pointer items-center gap-2 text-sm font-semibold text-foreground">
          <FileCode2 size={15} className="text-primary" aria-hidden="true" />
          {t("profiles.ontologyBuild.mermaidTitle")}
        </summary>
        <div className="mt-2 grid gap-2">
          <p className="text-xs text-muted">{t("profiles.ontologyBuild.mermaidHint")}</p>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              loading={busy === "mermaid"}
              onClick={() => void loadMermaid()}
            >
              <FileCode2 size={15} aria-hidden="true" />
              <span>{t("profiles.ontologyBuild.mermaidLoad")}</span>
            </Button>
            {mermaid ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-label={t("profiles.ontologyBuild.mermaidCopy")}
                onClick={() => void copyMermaid()}
              >
                <ClipboardCopy size={15} aria-hidden="true" />
                <span>
                  {mermaidCopied
                    ? t("profiles.ontologyBuild.mermaidCopied")
                    : t("profiles.ontologyBuild.mermaidCopy")}
                </span>
              </Button>
            ) : null}
          </div>
          {mermaid ? (
            <pre
              className="max-h-80 overflow-auto rounded-md border border-border bg-code p-3 font-mono text-xs leading-5 text-code-fg"
              data-testid="ontology-build-mermaid"
            >
              <code>{mermaid}</code>
            </pre>
          ) : null}
        </div>
      </details>
    </section>
  );
}

function SectionHeading() {
  return (
    <div>
      <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
        <Sparkles size={16} className="text-primary" aria-hidden="true" />
        {t("profiles.ontologyBuild.title")}
      </h3>
      <p className="mt-1 text-sm leading-6 text-muted">{t("profiles.ontologyBuild.hint")}</p>
    </div>
  );
}
