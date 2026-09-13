import {
  Banner,
  Button,
  StatusBadge,
  FieldError,
} from "@engchina/production-ready-ui";
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiError, apiGet, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { useWorkspaceState, useResetExecutionConsent } from "@/components/WorkspaceState";
import { ContentActionBar } from "@/components/ContentActionBar";
import type { OntologyMarkdownState, OntologyPublishJob } from "./types";
import { DefinitionFields, TechnicalDetails } from "./ontologyResultPresentation";

type Definition = Record<string, unknown>;
interface Preparation {
  id: string; status: string; draft_etag: string; expected_head: string; display_version: number;
  error_message_ja: string;
  findings: { severity: string; message_ja?: string; message?: string }[];
  differences: { id: string; before: Definition | null; after: Definition | null }[];
  data_report?: Record<string, unknown>;
}
interface Migration { id: string; markdown: string; draft_etag: string; conflicts: string[]; applied: boolean }

export function MarkdownPublication({ profileId, profileLabel, signature, disabled, save, onPublished, onMigrated, onBusyChange }: {
  profileId: string; profileLabel: string; signature: string; disabled: boolean;
  save: () => Promise<OntologyMarkdownState | null | undefined>;
  onPublished: (job: OntologyPublishJob) => void;
  onMigrated: (state: OntologyMarkdownState) => void;
  onBusyChange: (busy:string) => void;
}) {
  const endpoint = `/api/nl2sql/profiles/${encodeURIComponent(profileId)}/ontology-markdown`;
  const [preparationId, setPreparationId] = useWorkspaceState(`markdown:${profileId}:preparation`, "");
  const [execution, setExecution] = useWorkspaceState(`markdown:${profileId}:publication-recovery`, { key: "" });
  const [acceptance, setAcceptance] = useWorkspaceState(`markdown:${profileId}:acceptance`, "");
  const [error, setError] = useState("");
  const [acceptanceError, setAcceptanceError] = useState("");
  const [busy, setBusy] = useState("");
  const [migration, setMigration] = useState<Migration | null>(null);
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
      setMigration(null);
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
      const result = await apiPost<Preparation>(`${endpoint}/prepare`, { draft_etag: state.draft_etag }, headers(crypto.randomUUID()));
      if (!mounted.current) return;
      setPreparationId(result.id);
    } catch (e) { setError(e instanceof Error ? e.message : t("markdownOntology.failed")); }
    finally { setBusy(""); }
  }
  async function publish() {
    if (!value || value.status !== "ready" || preparation.isError) return;
    const consent = generation.current;
    if (!(await confirm({title:t("profiles.ontologyBuild.publish"), description:t("markdownOntology.confirm", {profile:profileLabel,version:String(value.display_version)}),confirmLabel:t("profiles.ontologyBuild.publish"),tone:"info"}))) return;
    if (consent !== generation.current) return;
    setBusy("publish"); setError("");
    const body = { preparation_id: value.id, draft_etag: value.draft_etag, expected_head: value.expected_head, confirmed: true };
    const key = crypto.randomUUID();
    setExecution({key});
    try {
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
  async function previewMigration() {
    setBusy("migration");setError("");
    try { const saved = await save(); if (!saved) return; setMigration(await apiPost<Migration>(`${endpoint}/migration-preview`, {})); }
    catch(e) {setError(e instanceof Error ? e.message : t("markdownOntology.failed"));}
    finally {setBusy("");}
  }
  async function applyMigration() {
    if (!migration) return;
    const consent = generation.current;
    if (!(await confirm({title:t("markdownOntology.import"),description:t("markdownOntology.importHint"),confirmLabel:t("markdownOntology.import"),tone:"info"}))) return;
    if (consent !== generation.current) return;
    setBusy("migration");setError("");
    try { const result = await apiPost<OntologyMarkdownState>(`${endpoint}/migrate`, {preview_id:migration.id,draft_etag:migration.draft_etag}); if (!mounted.current) return; onMigrated(result); setMigration(null); }
    catch(e) {setError(e instanceof Error ? e.message : t("markdownOntology.failed"));}
    finally {setBusy("");}
  }
  return <div className="grid min-w-0 gap-3 border-t border-border pt-4" data-testid="ontology-publish-actions">
    <ContentActionBar ariaLabel={t("markdownOntology.actions")}>
      <Button type="button" variant="primary" size="md" disabled={disabled || Boolean(busy) || running || Boolean(execution.key)} loading={busy === "prepare" || running} onClick={() => void prepare()}>{t("profiles.ontologyBuild.publish")}</Button>
      <Button type="button" variant="secondary" size="sm" disabled={Boolean(busy) || running} onClick={() => void previewMigration()}>{t("markdownOntology.importPreview")}</Button>
    </ContentActionBar>
    {(error || preparation.isError) && <Banner severity="danger">{error || t("markdownOntology.refreshFailed")}</Banner>}
    {execution.key && <Button type="button" size="sm" variant="secondary" disabled={Boolean(busy)} onClick={() => void recover()}>{t("markdownOntology.checkOutcome")}</Button>}
    {value && <section className="grid min-w-0 gap-3" aria-label={t("markdownOntology.check")}>
      <h3 className="text-sm font-semibold">{t("markdownOntology.check")}</h3>
      <StatusBadge variant={dataValidationFailed || value.status === "failed" ? "danger" : value.status === "ready" ? "success" : "info"} label={t(`markdownOntology.status.${dataValidationFailed ? "failed" : value.status}`)} />
      {value.error_message_ja && <Banner severity="danger">{value.error_message_ja}</Banner>}
      {value.findings?.map((f,i)=><p key={i} className="break-words text-sm">{f.message_ja || f.message}</p>)}
      {value.differences?.map(d=><details key={d.id} className="min-w-0 rounded border border-border p-3">
        <summary className="cursor-pointer text-sm">{String((d.after || d.before)?.name_ja ?? d.id)} · {t(d.before ? d.after ? "markdownOntology.changed" : "markdownOntology.removed" : "markdownOntology.added")}</summary>
        <div className="grid min-w-0 gap-3 py-3 md:grid-cols-2"><div><p>{t("markdownOntology.before")}</p><DefinitionFields definition={d.before ?? {}} /></div><div><p>{t("markdownOntology.after")}</p><DefinitionFields definition={d.after ?? {}} /></div></div>
      </details>)}
      {value.status === "ready" && <>
        <details><summary className="cursor-pointer text-sm">{t("markdownOntology.dataValidation")}</summary>
          <label className="grid gap-2 text-sm">{t("markdownOntology.acceptance")}<textarea className="min-h-24 w-full rounded border border-border-control bg-surface p-3" value={acceptance} aria-invalid={Boolean(acceptanceError)} aria-describedby="markdown-acceptance-hint markdown-acceptance-error" onChange={e=>{setAcceptance(e.target.value);setAcceptanceError("");}} /></label>
          <p id="markdown-acceptance-hint" className="text-xs text-fg-muted">{t("markdownOntology.acceptanceHint")}</p>
          <FieldError id="markdown-acceptance-error" message={acceptanceError} />
          <Button type="button" variant="secondary" size="sm" disabled={Boolean(busy)} onClick={()=>void dataValidation()}>{t("markdownOntology.dataValidation")}</Button>
          {value.data_report && <div className="grid gap-3">
            <DefinitionFields definition={Object.fromEntries(["checked_at", "sample_limit", "instance_count", "errors", "acceptance_cases"].filter(key => key in value.data_report!).map(key => [key, value.data_report![key]]))} />
            <p className="text-xs text-fg-muted">{t("markdownOntology.sampledOnly")}</p>
            <TechnicalDetails value={value.data_report} />
          </div>}
        </details>
        {dataValidationFailed && <Banner severity="danger">{t("markdownOntology.dataValidationFailed")}</Banner>}
        <ContentActionBar ariaLabel={t("markdownOntology.actions")}><Button type="button" variant="primary" size="md" disabled={Boolean(busy) || Boolean(execution.key) || preparation.isError || dataValidationFailed} loading={busy === "publish"} onClick={()=>void publish()}>{t("markdownOntology.confirmPublish")}</Button></ContentActionBar>
      </>}
    </section>}
    {migration && <section className="grid gap-3" aria-label={t("markdownOntology.importPreview")}>
      <p>{t("markdownOntology.importHint")}</p>
      <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded border border-border p-3 text-xs">{migration.markdown}</pre>
      {migration.conflicts.map((c,i)=><p key={i}>{c}</p>)}
      <Button type="button" variant="secondary" size="sm" disabled={Boolean(busy) || migration.applied} onClick={()=>void applyMigration()}>{t("markdownOntology.import")}</Button>
    </section>}
  </div>;
}
