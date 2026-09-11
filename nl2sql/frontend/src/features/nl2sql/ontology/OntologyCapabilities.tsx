import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { FormStatus } from "@/components/ui/form-status";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { useResetExecutionConsent, useWorkspaceState } from "@/components/WorkspaceState";
import { apiGet, apiPatch, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";

interface Parameter { api_name: string; name_ja: string; data_type: string; required: boolean }
interface Capability { definition: { id: string; kind: string; name_ja: string; api_name: string; description_ja: string; expression_sql?: string; implementation_key?: string; parameters: Parameter[] }; target_parameters: Parameter[]; status: string; reason_ja: string; binding: { etag: string; kind: string; expression_sql: string; implementation_key: string; state_requirements: unknown[]; reviewed_rules_ja: string; enabled: boolean } | null }
interface Catalog { release_id: string; capabilities: Capability[]; implementations: { functions: string[]; actions: string[] } }
interface Preview { id: string; before: Record<string, unknown>; after: Record<string, unknown>; expires_at: string }
interface Execution { id: string; release_id: string; at: string; status: string; result?: unknown; before?: Record<string, unknown>; after?: Record<string, unknown> }

export function OntologyCapabilities({ profileId }: { profileId: string }) {
  const endpoint = `/api/nl2sql/profiles/${encodeURIComponent(profileId)}/ontology-capabilities`;
  const query = useQuery({ queryKey: ["nl2sql", "profiles", "ontology-capabilities", profileId], queryFn: ({ signal }) => apiGet<Catalog>(endpoint, { signal }), retry: false });
  return <section aria-label={t("ontologyCapability.title")} className="grid min-w-0 grid-cols-1 gap-3">
    <p className="text-sm text-muted">{t("ontologyCapability.hint")}</p>
    <Button size="sm" variant="secondary" onClick={() => void query.refetch()} loading={query.isFetching}><span className="block w-full whitespace-normal break-words">{t("ontologyCapability.refresh")}</span></Button>
    {query.isPending ? <p role="status">{t("ontologyCapability.loading")}</p> : null}
    {query.isError ? <FormStatus tone="danger" message={t("ontologyWorkspace.loadError")} /> : null}
    {query.data ? <><p className="break-all text-xs">{t("ontologyWorkspace.published")}: {query.data.release_id || t("ontologyResults.unspecified")}</p>{query.data.capabilities.length ? query.data.capabilities.map(capability => <CapabilityCard key={`${profileId}:${query.data!.release_id}:${capability.definition.id}:${capability.binding?.etag ?? ""}`} profileId={profileId} releaseId={query.data!.release_id} capability={capability} implementations={capability.definition.kind === "function" ? query.data!.implementations.functions : query.data!.implementations.actions} refresh={() => query.refetch()} stale={query.isError || query.isFetching} />) : <p>{t("ontologyCapability.empty")}</p>}</> : null}
  </section>;
}

function CapabilityCard({ profileId, releaseId, capability, implementations, refresh, stale }: { profileId: string; releaseId: string; capability: Capability; implementations: string[]; refresh: () => Promise<unknown>; stale: boolean }) {
  const definition = capability.definition;
  const isFunction = definition.kind === "function";
  const prefix = `ontology-capability:${profileId}:${releaseId}:${definition.id}`;
  const endpoint = `/api/nl2sql/profiles/${encodeURIComponent(profileId)}/ontology-capabilities/${encodeURIComponent(definition.id)}`;
  const [values, setValues] = useWorkspaceState<Record<string, string>>(`${prefix}:parameters`, {});
  const [targets, setTargets] = useWorkspaceState<Record<string, string>>(`${prefix}:target`, {});
  const [kind, setKind] = useWorkspaceState(`${prefix}:binding-kind`, capability.binding?.kind ?? (isFunction ? "expression" : "property_update"));
  const [sql, setSql] = useWorkspaceState(`${prefix}:sql`, capability.binding?.expression_sql ?? definition.expression_sql ?? "");
  const [implementation, setImplementation] = useWorkspaceState(`${prefix}:implementation`, capability.binding?.implementation_key ?? definition.implementation_key ?? "");
  const [rules, setRules] = useWorkspaceState(`${prefix}:rules`, capability.binding?.reviewed_rules_ja ?? "");
  const [states, setStates] = useWorkspaceState(`${prefix}:states`, JSON.stringify(capability.binding?.state_requirements ?? [], null, 2));
  const [enabled, setEnabled] = useState(capability.binding?.enabled ?? true);
  const [lastId, setLastId] = useWorkspaceState(`${prefix}:last-id`, "");
  const [lastInput, setLastInput] = useWorkspaceState(`${prefix}:last-input`, "");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const confirm = useConfirm();
  const consent = useRef(0);
  const input = JSON.stringify({ values, targets });
  useResetExecutionConsent(() => { setPreview(null); consent.current += 1; }, `${prefix}:${input}:${kind}:${sql}:${rules}:${states}:${implementation}:${capability.binding?.etag ?? ""}`);
  const execution = useQuery({ queryKey: ["nl2sql", "profiles", "ontology-capability-execution", profileId, lastId], queryFn: ({ signal }) => apiGet<Execution>(`/api/nl2sql/profiles/${encodeURIComponent(profileId)}/ontology-capabilities/executions/${encodeURIComponent(lastId)}`, { signal }), enabled: !!lastId, retry: false });
  const convert = (parameters: Parameter[], entries: Record<string, string>) => Object.fromEntries(parameters.map(parameter => { const value = entries[parameter.api_name]; return [parameter.api_name, value === undefined || value === "" ? null : ["integer", "number"].includes(parameter.data_type) ? Number(value) : parameter.data_type === "boolean" ? value === "true" : parameter.data_type === "object" ? JSON.parse(value) : value]; }));
  const run = async (action: "bind" | "invoke" | "preview" | "execute") => {
    const current = consent.current;
    if (["bind", "execute", "invoke"].includes(action) && !await confirm({ title: t(`ontologyCapability.${action}`), description: t("ontologyWorkspace.confirmScope", { profile: profileId, version: releaseId }), confirmLabel: t("ontologyWorkspace.confirm"), tone: "info" })) return;
    if (current !== consent.current) return;
    setBusy(action); setError("");
    try {
      if (action === "bind") {
        await apiPatch(`${endpoint}/binding`, { release_id: releaseId, kind, expression_sql: sql, implementation_key: implementation, max_rows: 100, state_requirements: JSON.parse(states), reviewed_rules_ja: rules, enabled }, { "If-Match": capability.binding?.etag ?? "*" });
        setPreview(null); await refresh();
      } else {
        const body = action === "execute" ? { preview_id: preview?.id, confirmed: true } : { release_id: releaseId, parameters: convert(definition.parameters ?? [], values), target: convert(capability.target_parameters ?? [], targets) };
        const result = await apiPost<Preview | Execution>(`${endpoint}/${action}`, body, { headers: { "Idempotency-Key": crypto.randomUUID() } });
        if (action === "preview") { if (current === consent.current) setPreview(result as Preview); }
        else { setLastId(result.id); setLastInput(input); setPreview(null); }
      }
    } catch (cause) { setPreview(null); setError(cause instanceof Error ? cause.message : t("ontologyWorkspace.failed")); }
    finally { setBusy(""); }
  };
  const fields = (parameters: Parameter[], entries: Record<string, string>, update: (next: Record<string, string>) => void) => parameters.map(parameter => <label key={parameter.api_name} className="grid min-w-0 gap-1 text-sm">{parameter.name_ja} ({parameter.api_name}){parameter.required ? ` · ${t("ontologyCapability.required")}` : ""}{parameter.data_type === "boolean" ? <select value={entries[parameter.api_name] ?? ""} onChange={e => { setPreview(null); update({ ...entries, [parameter.api_name]: e.target.value }); }} className="rounded border border-border bg-background p-2"><option value="">{t("ontologyResults.unspecified")}</option><option value="true">{t("ontologyResults.yes")}</option><option value="false">{t("ontologyResults.no")}</option></select> : <input value={entries[parameter.api_name] ?? ""} onChange={e => { setPreview(null); update({ ...entries, [parameter.api_name]: e.target.value }); }} type={parameter.data_type === "date" ? "date" : "text"} inputMode={["integer", "number"].includes(parameter.data_type) ? "decimal" : "text"} className="min-w-0 rounded border border-border bg-background p-2" />}</label>);
  return <article className="grid min-w-0 grid-cols-1 gap-3 rounded border border-border p-3" aria-label={`${definition.name_ja} (${definition.api_name})`}>
    <h4 className="font-medium">{definition.name_ja} ({definition.api_name})</h4>
    <p className="text-sm">{t(capability.status === "available" ? "ontologyCapability.available" : "ontologyCapability.configuration")}</p>
    {capability.reason_ja ? <p className="text-sm text-muted">{capability.reason_ja}</p> : null}
    {error ? <FormStatus tone="danger" message={error} /> : null}
    <details className="min-w-0"><summary>{t("ontologyCapability.binding")}</summary><div className="grid min-w-0 grid-cols-1 gap-3 py-3">
      <label className="grid gap-1 text-sm">{t("ontologyCapability.kind")}<select value={kind} onChange={e => setKind(e.target.value)} className="min-w-0 rounded border border-border bg-background p-2">{(isFunction ? ["expression", "sql", "backend"] : ["property_update", "backend"]).map(value => <option key={value} value={value}>{t(`ontologyCapability.kind.${value}` as Parameters<typeof t>[0])}</option>)}</select></label>
      {kind === "backend" ? <label className="grid gap-1">{t("ontologyCapability.implementation")}<select value={implementation} onChange={e => setImplementation(e.target.value)} className="min-w-0 rounded border border-border bg-background p-2"><option value="">{t("ontologyResults.unspecified")}</option>{implementations.map(key => <option key={key} value={key}>{key}</option>)}</select></label> : isFunction ? <label className="grid gap-1">{t("ontologyCapability.expression")}<textarea value={sql} onChange={e => setSql(e.target.value)} className="min-h-24 min-w-0 rounded border border-border bg-background p-2 font-mono text-xs" /></label> : null}
      {!isFunction ? <><label className="grid gap-1">{t("ontologyCapability.rules")}<textarea value={rules} onChange={e => setRules(e.target.value)} className="min-h-20 min-w-0 rounded border border-border bg-background p-2" /></label><p className="text-xs text-muted">{t("ontologyCapability.statesHint")}</p><label className="grid gap-1">{t("ontologyCapability.states")}<textarea value={states} onChange={e => setStates(e.target.value)} className="min-h-20 min-w-0 rounded border border-border bg-background p-2 font-mono text-xs" /></label></> : null}
      <label className="flex items-center gap-2"><input type="checkbox" checked={enabled} onChange={e => { setEnabled(e.target.checked); setPreview(null); }} />{t("ontologyCapability.enabled")}</label>
      <Button size="md" variant="secondary" disabled={!!busy || stale} onClick={() => void run("bind")}><span className="block w-full whitespace-normal break-words">{t("ontologyCapability.bind")}</span></Button>
    </div></details>
    {capability.status === "available" ? <><fieldset className="grid min-w-0 gap-3"><legend className="mb-2 text-sm font-medium">{t("ontologyCapability.parameters")}</legend>{fields(definition.parameters ?? [], values, setValues)}{fields(capability.target_parameters ?? [], targets, setTargets)}</fieldset><Button size="md" variant="secondary" disabled={!!busy || stale} loading={busy === "preview" || busy === "invoke"} onClick={() => void run(isFunction ? "invoke" : "preview")}><span className="block w-full whitespace-normal break-words">{t(isFunction ? "ontologyCapability.invoke" : "ontologyCapability.preview")}</span></Button></> : null}
    {preview ? <div className="grid min-w-0 gap-3"><p className="text-xs">{t("ontologyCapability.expires")}: {preview.expires_at}</p><div className="overflow-auto"><table className="w-full text-left text-sm"><thead><tr><th>{t("ontologyCapability.property")}</th><th>{t("ontologyCapability.before")}</th><th>{t("ontologyCapability.after")}</th></tr></thead><tbody>{Object.keys(preview.before).map(name => <tr key={name}><td className="p-2">{name}</td><td className="p-2">{String(preview.before[name] ?? "")}</td><td className="p-2">{String(preview.after[name] ?? "")}</td></tr>)}</tbody></table></div><Button size="lg" variant="primary" disabled={!!busy || stale} loading={busy === "execute"} onClick={() => void run("execute")}><span className="block w-full whitespace-normal break-words">{t("ontologyCapability.execute")}</span></Button></div> : null}
    {lastId ? <div className="grid min-w-0 gap-2"><p className="text-sm">{t("ontologyCapability.previous")}: {execution.data?.at ?? ""}</p>{lastInput !== input ? <p className="text-sm text-muted">{t("ontologyCapability.unexecuted")}</p> : null}{execution.isError ? <FormStatus tone="danger" message={t("ontologyWorkspace.loadError")} /> : null}{execution.data ? <pre className="max-h-64 min-w-0 overflow-auto whitespace-pre-wrap break-words text-xs">{JSON.stringify(execution.data, null, 2)}</pre> : null}</div> : null}
  </article>;
}
