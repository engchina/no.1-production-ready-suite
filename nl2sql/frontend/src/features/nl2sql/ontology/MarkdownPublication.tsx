import { RefreshCw, ShieldCheck, Upload } from "lucide-react";
import {
  Banner,
  Button,
  StatusBadge,
  FieldError,
  useConfirm,
} from "@engchina/production-ready-ui";
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiError, apiGet, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import { randomUuid } from "@/lib/randomUuid";
import { useWorkspaceState, useResetExecutionConsent } from "@/components/WorkspaceState";
import { DisclosureChevron } from "@/components/ui/disclosure-chevron";
import { ContentActionBar } from "@/components/ContentActionBar";
import type { OntologyFinding, OntologyMarkdownState, OntologyPublishJob } from "./types";
import { OntologyFindings } from "./OntologyFindings";
import { DefinitionFields, TechnicalDetails } from "./ontologyResultPresentation";

type Definition = Record<string, unknown>;
interface Preparation {
  id: string; status: string; draft_etag: string; expected_head: string; display_version: number;
  error_message_ja: string;
  findings: OntologyFinding[];
  differences: { id: string; before: Definition | null; after: Definition | null }[];
  data_report?: Record<string, unknown>;
}

export function MarkdownPublication({ profileId, profileLabel, signature, disabled, save, onPublished, onBusyChange }: {
  profileId: string; profileLabel: string; signature: string; disabled: boolean;
  save: () => Promise<OntologyMarkdownState | null | undefined>;
  onPublished: (job: OntologyPublishJob) => void;
  onBusyChange: (busy:string) => void;
}) {
  const endpoint = `/api/nl2sql/profiles/${encodeURIComponent(profileId)}/ontology-markdown`;
  const [preparationId, setPreparationId] = useWorkspaceState(`markdown:${profileId}:preparation`, "");
  const [execution, setExecution] = useWorkspaceState(`markdown:${profileId}:publication-recovery`, { key: "" });
  const [acceptance, setAcceptance] = useWorkspaceState(`markdown:${profileId}:acceptance`, "");
  const [error, setError] = useState("");
  const [acceptanceError, setAcceptanceError] = useState("");
  const [busy, setBusy] = useState("");
  const generation = useRef(0);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; generation.current += 1; };
  }, []);
  useResetExecutionConsent(() => { generation.current += 1; }, `${profileId}:${signature}`);
  const lastSignature = useRef(signature);
  useEffect(() => {
    if (signature !== lastSignature.current) {
      lastSignature.current = signature;
      setPreparationId("");
    }
  }, [signature, setPreparationId]);
  const confirm = useConfirm();
  const preparation = useQuery({
    queryKey: ["markdown-preparation", profileId, preparationId],
    queryFn: ({signal}) => apiGet<Preparation>(`${endpoint}/preparations/${encodeURIComponent(preparationId)}`, {signal}),
    enabled: Boolean(preparationId),
    refetchInterval: query => ["queued", "running"].includes(query.state.data?.status ?? "") ? 1000 : false,
  });
  const value = preparation.data;
  const findings = [...(value?.findings ?? []), ...(value?.error_message_ja ? [{ severity: "error", message_ja: value.error_message_ja }] : [])];
  const hasErrors = findings.some(finding => finding.severity === "error");
  const hasWarnings = findings.some(finding => finding.severity === "warning");
  const dataValidationFailed = Number(value?.data_report?.errors ?? 0) > 0;
  const running = Boolean(value && ["queued", "running"].includes(value.status));
  useEffect(() => {
    onBusyChange(busy || running ? "markdown-check" : "");
    return () => onBusyChange("");
  }, [busy, running, onBusyChange]);
  const headers = (key: string) => ({ headers: { "Idempotency-Key": key } });
  async function prepare() {
    setBusy("prepare"); setError("");
    try {
      const state = await save();
      if (!state?.draft_etag) return;
      const result = await apiPost<Preparation>(`${endpoint}/prepare`, { draft_etag: state.draft_etag }, headers(randomUuid()));
      if (!mounted.current) return;
      setPreparationId(result.id);
    } catch (e) { setError(e instanceof Error ? e.message : t("markdownOntology.failed")); }
    finally { setBusy(""); }
  }
  async function publish() {
    if (!value || value.status !== "ready" || preparation.isError || hasErrors || dataValidationFailed) return;
    const consent = generation.current;
    if (!(await confirm({title:t("profiles.ontologyBuild.publish"), description:t("markdownOntology.confirm", {profile:profileLabel,version:String(value.display_version)}),confirmLabel:t("profiles.ontologyBuild.publish"),tone:"info"}))) return;
    if (consent !== generation.current) return;
    setBusy("publish"); setError("");
    const body = { preparation_id: value.id, draft_etag: value.draft_etag, expected_head: value.expected_head, confirmed: true };
    try {
      const key = randomUuid();
      setExecution({key});
      const result = await apiPost<{job:OntologyPublishJob}>(`${endpoint}/publish`, body, headers(key));
      if (!mounted.current) return;
      setExecution({key:""}); setPreparationId(""); onPublished(result.job);
    } catch (e) {
      if (e instanceof ApiError && e.status >= 400 && e.status < 500) setExecution({key:""});
      setError(e instanceof Error ? e.message : t("markdownOntology.failed"));
    }
    finally { setBusy(""); }
  }
  async function recover() {
    setBusy("recover"); setError("");
    try {
      const result = await apiGet<{job:OntologyPublishJob | null}>(`${endpoint}/publication-outcome?key=${encodeURIComponent(execution.key)}`);
      if (!mounted.current) return;
      if (result.job) { setExecution({key:""}); onPublished(result.job); }
      else setError(t("markdownOntology.unknownOutcome"));
    } catch(e) { setError(e instanceof Error ? e.message : t("markdownOntology.failed")); }
    finally { setBusy(""); }
  }
  async function dataValidation() {
    let cases: unknown = [];
    try { cases = acceptance.trim() ? JSON.parse(acceptance) : []; if (!Array.isArray(cases)) throw new Error(); }
    catch {setAcceptanceError(t("ontologyUi.jsonError")); return;}
    setAcceptanceError("");
    setBusy("data"); setError("");
    try { await apiPost(`${endpoint}/preparations/${value?.id}/validate-data`, { confirmed: true, acceptance_cases: cases }); await preparation.refetch(); }
    catch(e) {setError(e instanceof Error ? e.message : t("markdownOntology.failed"));}
    finally {setBusy("");}
  }
  return <div className="grid min-w-0 gap-3 border-t border-border pt-4" data-testid="ontology-publish-actions">
    <ContentActionBar ariaLabel={t("markdownOntology.actions")}>
      <Button icon={Upload} type="button" variant="primary" size="lg" disabled={disabled || Boolean(busy) || running || Boolean(execution.key)} loading={busy === "prepare" || running} onClick={() => void prepare()}>{t("profiles.ontologyBuild.publish")}</Button>
    </ContentActionBar>
    {(error || preparation.isError) && <Banner severity="danger"><div tabIndex={0} className="max-h-72 overflow-y-auto break-words">{error || t("markdownOntology.refreshFailed")}</div></Banner>}
    {execution.key && <Button icon={RefreshCw} type="button" size="sm" variant="secondary" disabled={Boolean(busy)} onClick={() => void recover()}>{t("markdownOntology.checkOutcome")}</Button>}
    {value && <section className="grid min-w-0 gap-3" aria-label={t("markdownOntology.check")}>
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-3">
      <h3 className="text-sm font-semibold">{t("markdownOntology.check")}</h3>
      <StatusBadge variant={hasErrors || dataValidationFailed || value.status === "failed" ? "danger" : value.status === "ready" ? hasWarnings ? "warning" : "success" : "info"} label={t(`markdownOntology.status.${hasErrors || dataValidationFailed ? "failed" : value.status}`)} />
      </div>
      {hasErrors && <Banner severity="danger">{t("markdownOntology.errorsBlockPublish")}</Banner>}
      {value.status === "ready" && hasWarnings && !hasErrors && !dataValidationFailed && <Banner severity="warning">{t("markdownOntology.warningsPublishable")}</Banner>}
      <OntologyFindings findings={findings} label={t("markdownOntology.findings")} />
      {value.differences?.map(d=><details key={d.id} className="group/disclosure min-w-0 rounded-md border border-border bg-surface" data-testid="ontology-publication-difference">
        <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-medium text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring [&::-webkit-details-marker]:hidden">
          <span className="min-w-0 flex-1 break-words">{String((d.after || d.before)?.name_ja ?? d.id)}</span>
          <StatusBadge variant={d.before ? d.after ? "info" : "danger" : "success"} label={t(d.before ? d.after ? "markdownOntology.changed" : "markdownOntology.removed" : "markdownOntology.added")} />
          <DisclosureChevron expanded="group" size={16} className="text-fg-muted" />
        </summary>
        <div className="grid min-w-0 gap-4 border-t border-border p-4 md:grid-cols-2">
          <div className="min-w-0 space-y-3"><h4 className="text-sm font-semibold text-fg-muted">{t("markdownOntology.before")}</h4><DefinitionFields definition={d.before ?? {}} /></div>
          <div className="min-w-0 space-y-3"><h4 className="text-sm font-semibold text-fg-muted">{t("markdownOntology.after")}</h4><DefinitionFields definition={d.after ?? {}} /></div>
        </div>
      </details>)}
      {value.status === "ready" && <>
        <details className="group/disclosure min-w-0 rounded-md border border-border bg-surface" data-testid="ontology-publication-data-validation">
          <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-medium text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring [&::-webkit-details-marker]:hidden">
            <span className="flex min-w-0 items-center gap-2"><ShieldCheck size={16} className="shrink-0 text-fg-muted" aria-hidden="true" />{t("markdownOntology.dataValidation")}</span>
            <DisclosureChevron expanded="group" size={16} className="text-fg-muted" />
          </summary>
          <div className="grid min-w-0 gap-4 border-t border-border p-4">
            <div className="space-y-2">
              <label htmlFor="markdown-acceptance" className="block text-sm font-medium text-fg">{t("markdownOntology.acceptance")}</label>
              <textarea id="markdown-acceptance" rows={5} className="min-h-32 w-full resize-y rounded-md border border-border-control bg-surface px-3 py-2 font-mono text-sm leading-6 text-fg outline-none focus:border-focus-ring focus:ring-2 focus:ring-focus-ring" value={acceptance} aria-invalid={Boolean(acceptanceError)} aria-describedby="markdown-acceptance-hint markdown-acceptance-error" onChange={e=>{setAcceptance(e.target.value);setAcceptanceError("");}} />
              <p id="markdown-acceptance-hint" className="text-xs leading-relaxed text-fg-muted">{t("markdownOntology.acceptanceHint")}</p>
              <FieldError id="markdown-acceptance-error" message={acceptanceError} />
            </div>
            <ContentActionBar ariaLabel={t("markdownOntology.dataValidation")}>
              <Button icon={ShieldCheck} type="button" variant="secondary" size="md" disabled={Boolean(busy)} loading={busy === "data"} onClick={()=>void dataValidation()}>{t("markdownOntology.dataValidation")}</Button>
            </ContentActionBar>
            {value.data_report && <div className="grid min-w-0 gap-3 border-t border-border pt-4">
              <DefinitionFields definition={Object.fromEntries(["checked_at", "sample_limit", "instance_count", "errors", "acceptance_cases"].filter(key => key in value.data_report!).map(key => [key, value.data_report![key]]))} />
              <p className="text-xs leading-relaxed text-fg-muted">{t("markdownOntology.sampledOnly")}</p>
              <TechnicalDetails value={value.data_report} />
            </div>}
          </div>
        </details>
        {dataValidationFailed && <Banner severity="danger">{t("markdownOntology.dataValidationFailed")}</Banner>}
        <ContentActionBar ariaLabel={t("markdownOntology.actions")}><Button icon={Upload} type="button" variant="primary" size="lg" disabled={Boolean(busy) || Boolean(execution.key) || preparation.isError || hasErrors || dataValidationFailed} loading={busy === "publish"} onClick={()=>void publish()}>{t("markdownOntology.confirmPublish")}</Button></ContentActionBar>
      </>}
    </section>}
  </div>;
}
