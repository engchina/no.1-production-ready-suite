import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { FormStatus } from "@/components/ui/form-status";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { useResetExecutionConsent, useWorkspaceState } from "@/components/WorkspaceState";
import { apiGet, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import { ProfileOntologyGraph } from "./ProfileOntologyGraph";
import type { ProfileOntologyBundle } from "./ProfileOntologyResults";

const tabs = ["overview", "model", "mapping", "validation", "review"] as const;
type Tab = typeof tabs[number];
interface Workspace { bundle: ProfileOntologyBundle; artifacts: Record<string, string>; head: { release_id: string; etag: string }; releases?: { id: string; published_at: string }[] }
interface Changes { id: string; base_etag: string; before: Record<string, unknown>[]; after: Record<string, unknown>[] }
interface ValidationJob { job_id: string; status: string; error_message_ja?: string; report?: Record<string, unknown> }

export function OntologyDefinitionWorkspace({ bundle, profileId, onChanged, children }: { bundle: ProfileOntologyBundle; profileId: string; onChanged: () => Promise<unknown>; children: ReactNode }) {
  const prefix = `ontology-v2:${profileId}:${bundle.id}`;
  const [tab, setTab] = useWorkspaceState<Tab>(`${prefix}:tab`, "overview");
  const [instruction, setInstruction] = useWorkspaceState(`${prefix}:instruction`, "");
  const [notes, setNotes] = useWorkspaceState(`${prefix}:notes`, bundle.notes_ja ?? "");
  const [acceptance, setAcceptance] = useWorkspaceState(`${prefix}:acceptance`, "");
  const [validationJobId, setValidationJobId] = useWorkspaceState(`${prefix}:validation-job`, "");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [changes, setChanges] = useState<Changes | null>(null);
  const [rollbackId, setRollbackId] = useState("");
  const confirm = useConfirm();
  const consentVersion = useRef(0);
  useResetExecutionConsent(() => { setChanges(null); setRollbackId(""); consentVersion.current += 1; }, `${profileId}:${bundle.etag}`);
  const workspace = useQuery({ queryKey: ["nl2sql", "profiles", "ontology-workspace", profileId, bundle.id, bundle.etag], queryFn: ({ signal }) => apiGet<Workspace>(`/api/nl2sql/profiles/${encodeURIComponent(profileId)}/ontology-results/${encodeURIComponent(bundle.id)}/workspace`, { signal }), retry: false });
  const job = useQuery({ queryKey: ["nl2sql", "profiles", "ontology-validation-job", profileId, validationJobId], queryFn: ({ signal }) => apiGet<ValidationJob>(`/api/nl2sql/profiles/${encodeURIComponent(profileId)}/ontology-validation-jobs/${encodeURIComponent(validationJobId)}`, { signal }), enabled: !!validationJobId, retry: false, refetchInterval: query => query.state.data && ["succeeded", "failed", "cancelled"].includes(query.state.data.status) ? false : 1000 });
  const handledJob = useRef("");
  useEffect(() => {
    if (job.data?.status === "succeeded" && handledJob.current !== job.data.job_id) {
      handledJob.current = job.data.job_id;
      void onChanged();
    }
  }, [job.data, onChanged]);
  const endpoint = `/api/nl2sql/profiles/${encodeURIComponent(profileId)}/ontology-results/${encodeURIComponent(bundle.id)}`;
  const readOnly = bundle.status === "published";
  const headers = () => ({ "If-Match": `"${bundle.etag}"`, "Idempotency-Key": crypto.randomUUID() });
  const run = async (action: string, body?: unknown, needsConfirmation = false) => {
    const consent = consentVersion.current;
    if (needsConfirmation && !await confirm({ title: t(`ontologyWorkspace.action.${action}` as Parameters<typeof t>[0]), description: t("ontologyWorkspace.confirmScope", { profile: profileId, version: bundle.id }), confirmLabel: t("ontologyWorkspace.confirm"), tone: "info" })) return;
    if (consent !== consentVersion.current) return;
    setBusy(action); setError("");
    try {
      if (action === "analyze") setChanges(await apiPost<Changes>(`${endpoint}/analyze`, { instruction_ja: instruction }, { headers: headers() }));
      else if (action === "rollback") {
        await apiPost(`/api/nl2sql/profiles/${encodeURIComponent(profileId)}/ontology-releases/${encodeURIComponent(rollbackId)}/rollback`, { expected_head: workspace.data?.head?.release_id ?? "", confirmed: true }, { headers: { ...headers(), "If-Match": workspace.data?.head?.etag ?? "" } });
        setRollbackId(""); await workspace.refetch(); await onChanged();
      } else if (action === "data") {
        const cases: unknown = acceptance.trim() ? JSON.parse(acceptance) : [];
        const result = await apiPost<ValidationJob>(`${endpoint}/validation-jobs`, { confirmed: true, sample_limit: 50, acceptance_cases: cases }, { headers: headers() });
        setValidationJobId(result.job_id);
      } else {
        const suffix = action === "apply" && changes ? `changes/${encodeURIComponent(changes.id)}/apply` : action;
        await apiPost(`${endpoint}/${suffix}`, body, { headers: headers() });
        setChanges(null);
        await onChanged();
        await workspace.refetch();
      }
    } catch (cause) { setError(cause instanceof Error ? cause.message : t("ontologyWorkspace.failed")); }
    finally { setBusy(""); }
  };
  const renderJson = (value: unknown) => <pre className="max-h-96 min-w-0 overflow-auto whitespace-pre-wrap break-words rounded border border-border p-3 text-xs [overflow-wrap:anywhere]">{JSON.stringify(value, null, 2)}</pre>;
  return <div className="grid min-w-0 grid-cols-1 gap-3 [overflow-wrap:anywhere] [&_button]:h-auto [&_button]:min-h-11 [&_button]:max-w-full [&_button]:whitespace-normal [&_button]:py-2 [&_button>span]:whitespace-normal [&_button>span]:text-clip" data-testid="ontology-definition-workspace">
    <div role="tablist" aria-label={t("ontologyWorkspace.tabs")} className="flex min-w-0 flex-wrap gap-2">{tabs.map(name => <Button key={name} className="w-full sm:w-auto" id={`${prefix}-${name}-tab`} role="tab" aria-selected={tab === name} aria-controls={`${prefix}-panel`} variant={tab === name ? "primary" : "secondary"} size="sm" onClick={() => setTab(name)}>{t(`ontologyWorkspace.tab.${name}`)}</Button>)}</div>
    {error ? <FormStatus tone="danger" message={error} /> : null}
    {workspace.isError ? <FormStatus tone="danger" message={t("ontologyWorkspace.loadError")} /> : null}
    <div role="tabpanel" id={`${prefix}-panel`} aria-labelledby={`${prefix}-${tab}-tab`} className="grid min-w-0 grid-cols-1 gap-3">
      {tab === "model" ? children : null}
      {tab === "overview" ? <>
        <p>{t("ontologyWorkspace.overview", { count: bundle.definitions.length, unreviewed: bundle.definitions.filter(d => d.review_status === "unreviewed").length, conflicts: bundle.conflicts.length })}</p>
        <p className="break-all text-sm">{t("ontologyWorkspace.version")}: {bundle.id}</p>
        <p className="break-all text-sm">{t("ontologyWorkspace.published")}: {workspace.data?.head?.release_id || t("ontologyResults.unspecified")}</p>
        <p className="text-sm text-muted">{t("ontologyWorkspace.canonical")}</p>
        {workspace.data?.releases?.length ? <div className="grid gap-2"><label className="grid gap-1">{t("ontologyWorkspace.rollbackVersion")}<select value={rollbackId} onChange={e => setRollbackId(e.target.value)} className="min-w-0 rounded border border-border bg-background p-2"><option value="">{t("ontologyResults.unspecified")}</option>{workspace.data.releases.map(release => <option key={release.id} value={release.id}>{release.published_at} · {release.id}</option>)}</select></label><Button size="sm" variant="secondary" disabled={!rollbackId || !!busy || rollbackId === workspace.data?.head?.release_id} onClick={() => void run("rollback", undefined, true)}>{t("ontologyWorkspace.action.rollback")}</Button></div> : null}
        {workspace.data?.artifacts?.graph_json ? <ProfileOntologyGraph artifact={workspace.data.artifacts.graph_json} /> : null}
        {workspace.data?.artifacts ? <details><summary>{t("ontologyWorkspace.artifacts")}</summary>{Object.entries(workspace.data.artifacts).map(([name, content]) => <details key={name}><summary>{name}</summary><pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words text-xs">{content}</pre></details>)}</details> : null}
      </> : null}
      {tab === "mapping" ? <ul className="grid gap-3">{bundle.definitions.filter(d => d.mappings.length).map(d => <li key={d.id} className="min-w-0 rounded border border-border p-3"><strong>{d.name_ja} ({d.api_name})</strong>{d.mappings.map((mapping, index) => <p key={index} className="break-all text-sm">{[mapping.owner, mapping.object_name, mapping.column_name].filter(Boolean).join(".")}{mapping.expression_sql ? `: ${mapping.expression_sql}` : ""}</p>)}</li>)}</ul> : null}
      {tab === "validation" ? <>
        <p>{t("ontologyResults.staticOnly")}</p>
        <Button size="md" variant="secondary" disabled={readOnly || !!busy} loading={busy === "validate"} onClick={() => void run("validate")}>{t("ontologyWorkspace.action.validate")}</Button>
        {bundle.findings.length ? <ul className="grid gap-2">{bundle.findings.map((finding, index) => <li key={index} className="rounded border border-border p-2 text-sm"><strong>{finding.severity === "error" ? t("ontologyWorkspace.blocker") : t("ontologyWorkspace.warning")}</strong> · {finding.message_ja}<p className="break-all text-muted">{bundle.definitions.find(d => d.id === finding.definition_id)?.name_ja ?? finding.definition_id} · {finding.code}</p></li>)}</ul> : <p>{t("ontologyWorkspace.noFindings")}</p>}
        {bundle.validation_report ? renderJson(bundle.validation_report) : null}
        <details><summary>{t("ontologyWorkspace.acceptance")}</summary><p className="text-sm">{t("ontologyWorkspace.acceptanceHint")}</p><label className="grid gap-1">{t("ontologyWorkspace.acceptanceInput")}<textarea className="min-h-28 w-full rounded border border-border bg-background p-2" value={acceptance} onChange={e => setAcceptance(e.target.value)} /></label></details>
        <Button size="md" variant="secondary" disabled={readOnly || !!busy || !!job.data && ["queued", "claimed", "running"].includes(job.data.status)} onClick={() => void run("data", undefined, true)}>{t("ontologyWorkspace.action.data")}</Button>
        {job.data ? <p role="status">{t("ontologyWorkspace.dataJob")}: {t(`ontologyWorkspace.job.${job.data.status}` as Parameters<typeof t>[0])}</p> : null}
        {job.data?.error_message_ja ? <FormStatus tone="danger" message={job.data.error_message_ja} /> : null}
        {job.isError ? <FormStatus tone="danger" message={t("ontologyWorkspace.loadError")} /> : null}
      </> : null}
      {tab === "review" ? <>
        <label className="grid gap-1">{t("ontologyWorkspace.instruction")}<textarea value={instruction} onChange={e => { setInstruction(e.target.value); setChanges(null); }} className="min-h-28 w-full rounded border border-border bg-background p-2" disabled={readOnly || !!busy} /></label>
        <Button size="md" variant="secondary" disabled={readOnly || !!busy || !instruction.trim()} loading={busy === "analyze"} onClick={() => void run("analyze")}>{t("ontologyWorkspace.action.analyze")}</Button>
        {changes ? <div className="grid min-w-0 gap-3"><div className="grid min-w-0 gap-3 sm:grid-cols-2"><div className="min-w-0"><h4>{t("ontologyResults.current")}</h4>{renderJson(changes.before)}</div><div className="min-w-0"><h4>{t("ontologyResults.proposed")}</h4>{renderJson(changes.after)}</div></div><Button size="md" variant="secondary" disabled={!!busy || changes.base_etag.replaceAll('"', '') !== bundle.etag} onClick={() => void run("apply", { confirmed: true }, true)}>{t("ontologyWorkspace.action.apply")}</Button></div> : null}
        {bundle.conflicts.map((conflict, index) => <div key={index} className="grid min-w-0 gap-2 rounded border border-border p-3"><p>{conflict.current.name_ja} → {conflict.proposed.name_ja}</p><div className="flex flex-wrap gap-2">{(["current", "proposed"] as const).map(choice => <Button key={choice} size="sm" variant="secondary" disabled={readOnly || !!busy} onClick={() => void run(`conflicts/${index}/resolve`, { choice })}>{t(`ontologyResults.${choice}`)}</Button>)}</div></div>)}
        <label className="grid gap-1">{t("ontologyWorkspace.notes")}<textarea value={notes} onChange={e => setNotes(e.target.value)} className="min-h-20 rounded border border-border bg-background p-2" disabled={readOnly || !!busy} /></label>
        <Button size="sm" variant="secondary" disabled={readOnly || !!busy || notes === (bundle.notes_ja ?? "")} onClick={() => void run("notes", { notes_ja: notes })}>{t("ontologyWorkspace.action.notes")}</Button>
        <Button size="md" variant="secondary" disabled={readOnly || !!busy || !!bundle.conflicts.length} onClick={() => void run("review", { definition_ids: bundle.definitions.map(d => d.id), confirmed: true }, true)}>{t("ontologyWorkspace.action.review")}</Button>
        <Button size="md" variant="secondary" disabled={readOnly || !!busy} onClick={() => void run("validate")}>{t("ontologyWorkspace.action.validate")}</Button>
        <Button size="lg" variant="primary" disabled={readOnly || !!busy || workspace.isError || !workspace.data || bundle.definitions.some(d => d.review_status !== "reviewed") || !!bundle.conflicts.length || !!bundle.requires_revalidation || !bundle.validation_report?.kind || !!bundle.validation_report.errors} loading={busy === "publish"} onClick={() => void run("publish", { expected_head: workspace.data?.head?.release_id ?? "", confirmed: true }, true)}>{t("ontologyWorkspace.action.publish")}</Button>
        <p className="text-sm text-muted">{t("ontologyWorkspace.publishHint")}</p>
      </> : null}
    </div>
  </div>;
}
