import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { FormStatus } from "@/components/ui/form-status";
import { apiGet } from "@/lib/api";
import { t } from "@/lib/i18n";

export const conceptKinds = ["object_type", "property", "link_type", "function", "action_type", "interface", "shared_property", "value_type", "metric", "business_rule", "enumeration", "business_event", "object_set"] as const;
type ConceptKind = typeof conceptKinds[number];
export interface BusinessDefinition {
  id: string;
  kind: ConceptKind;
  api_name: string;
  name_ja: string;
  description_ja: string;
  review_status: "unreviewed" | "reviewed";
  missing_information_ja: string[];
  evidence: { source_id: string; locator: string; excerpt_ja: string; verified: boolean }[];
  mappings: { owner: string; object_name: string; column_name: string; expression_sql: string }[];
  [key: string]: unknown;
}
export interface ProfileOntologyBundle {
  id: string;
  profile_id: string;
  etag: string;
  status: "draft" | "published";
  created_at: string;
  parent_id: string;
  definitions: BusinessDefinition[];
  coverage: { kind: ConceptKind; count: number; status: "generated" | "insufficient_evidence" | "not_applicable" | "failed"; reason_ja: string }[];
  findings: { code: string; message_ja: string; definition_id: string; severity: string }[];
  conflicts: unknown[];
}

export function useProfileOntologyResults(profileId: string, buildId?: string) {
  return useQuery({
    queryKey: ["nl2sql", "profiles", "ontology-results", profileId, buildId ?? ""],
    queryFn: ({ signal }) => apiGet<{ results: ProfileOntologyBundle[] }>(
      `/api/nl2sql/profiles/${encodeURIComponent(profileId)}/ontology-results`, { signal }
    ),
    enabled: Boolean(profileId),
    retry: false,
  });
}

const kindLabel = (kind: ConceptKind) => t(`ontologyResults.kind.${kind}`);
const baseFields = new Set(["id", "kind", "api_name", "name_ja", "description_ja", "aliases", "review_status", "origin", "evidence", "mappings", "missing_information_ja"]);
function DefinitionValue({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === "") return <span>{t("ontologyResults.unspecified")}</span>;
  if (typeof value === "boolean") return <span>{t(value ? "ontologyResults.yes" : "ontologyResults.no")}</span>;
  if (Array.isArray(value)) return value.length ? <ul className="grid gap-1">{value.map((item, index) => <li key={index}><DefinitionValue value={item} /></li>)}</ul> : <span>{t("ontologyResults.unspecified")}</span>;
  if (typeof value === "object") return <dl className="grid gap-1">{Object.entries(value).map(([key, item]) => <div key={key}><dt className="font-medium">{fieldLabel(key)}</dt><dd className="pl-3"><DefinitionValue value={item} /></dd></div>)}</dl>;
  return <span className="break-words [overflow-wrap:anywhere]">{String(value)}</span>;
}
function fieldLabel(field: string) {
  const key = `ontologyResults.field.${field}` as Parameters<typeof t>[0];
  const translated = t(key);
  return translated === key ? field : translated;
}

export function ProfileOntologyResults({ profileId, buildId }: { profileId: string; buildId?: string }) {
  const query = useProfileOntologyResults(profileId, buildId);
  const [kind, setKind] = useState<ConceptKind>("object_type");
  const [search, setSearch] = useState("");
  const bundle = query.data?.results[0];
  const items = bundle?.definitions.filter(item => item.kind === kind && `${item.name_ja} ${item.api_name} ${item.description_ja}`.toLowerCase().includes(search.toLowerCase())) ?? [];
  return <section className="grid min-w-0 gap-3 rounded-md border border-border bg-background p-3" aria-label={t("ontologyResults.title")} data-testid="ontology-typed-results">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="font-semibold">{t("ontologyResults.title")}</h3>
      <Button variant="secondary" size="sm" onClick={() => void query.refetch()} disabled={query.isFetching}>{t("ontologyResults.refresh")}</Button>
    </div>
    {query.isPending ? <p role="status">{t("ontologyResults.loading")}</p> : null}
    {query.isError ? <FormStatus tone="danger" message={t("ontologyResults.error")} /> : null}
    {!query.isPending && !query.isError && !bundle ? <p>{t("ontologyResults.empty")}</p> : null}
    {bundle ? <>
      <p className="text-sm text-muted">{t(`ontologyResults.status.${bundle.status}`)} · {new Date(bundle.created_at).toLocaleString("ja-JP")}</p>
      <div className="flex flex-wrap gap-2" role="group" aria-label={t("ontologyResults.categories")}>
        {conceptKinds.map(item => <Button className="h-auto min-h-11 w-full whitespace-normal py-2 sm:w-auto [&>span]:whitespace-normal [&>span]:text-clip" key={item} variant={kind === item ? "primary" : "secondary"} size="sm" aria-pressed={kind === item} onClick={() => setKind(item)}><span>{kindLabel(item)} ({bundle.coverage.find(c => c.kind === item)?.count ?? 0})</span></Button>)}
      </div>
      <label className="grid gap-1 text-sm">{t("ontologyResults.search")}<input value={search} onChange={event => setSearch(event.target.value)} className="w-full rounded-md border border-border bg-background p-2" /></label>
      {!items.length ? <p>{bundle.coverage.find(item => item.kind === kind)?.reason_ja || t("ontologyResults.noMatch")}</p> : null}
      {items.map(item => <details key={item.id} className="min-w-0 rounded-md border border-border p-3">
        <summary className="cursor-pointer break-words font-medium">{item.name_ja} ({item.api_name})</summary>
        <div className="mt-3 grid min-w-0 gap-3 text-sm">
          <p>{item.description_ja}</p>
          <p>{t(`ontologyResults.status.${item.review_status}`)}</p>
          {item.missing_information_ja.length ? <ul className="text-warning">{item.missing_information_ja.map(message => <li key={message}>{message}</li>)}</ul> : null}
          <h4 className="font-medium">{t("ontologyResults.definition")}</h4>
          <dl className="grid min-w-0 gap-2">{Object.entries(item).filter(([key]) => !baseFields.has(key)).map(([key, value]) => <div key={key} className="grid min-w-0 gap-1 sm:grid-cols-[12rem_1fr]"><dt className="text-muted">{fieldLabel(key)}</dt><dd className="min-w-0"><DefinitionValue value={value} /></dd></div>)}</dl>
          <h4 className="font-medium">{t("ontologyResults.mapping")}</h4>
          {item.mappings.length ? item.mappings.map((mapping, i) => <p key={i} className="break-all">{[mapping.owner, mapping.object_name, mapping.column_name].filter(Boolean).join(".")}{mapping.expression_sql ? `: ${mapping.expression_sql}` : ""}</p>) : <p>{t("ontologyResults.unspecified")}</p>}
          <h4 className="font-medium">{t("ontologyResults.evidence")}</h4>
          {item.evidence.length ? item.evidence.map((evidence, i) => <blockquote key={i} className="border-l-2 border-border pl-3"><p>{evidence.excerpt_ja}</p><p className="break-all text-muted">{evidence.source_id} · {evidence.locator} · {t(evidence.verified ? "ontologyResults.verified" : "ontologyResults.unverified")}</p></blockquote>) : <p>{t("ontologyResults.unspecified")}</p>}
          <h4 className="font-medium">{t("ontologyResults.validation")}</h4>
          {bundle.findings.filter(f => f.definition_id === item.id).map((f, i) => <p key={i}>{f.message_ja}</p>)}
          <p className="text-muted">{t("ontologyResults.staticOnly")}</p>
        </div>
      </details>)}
    </> : null}
  </section>;
}
