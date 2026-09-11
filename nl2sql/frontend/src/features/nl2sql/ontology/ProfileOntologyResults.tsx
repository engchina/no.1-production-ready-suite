import { EmptyState, LoadingState, ErrorState } from "@/components/StateViews";
import { useCallback, useEffect } from "react";
import { Layers } from "lucide-react";
import { useWorkspaceState } from "@/components/WorkspaceState";
import { FixedSplitPane } from "@/components/layout/FixedSplitPane";
import { SelectField } from "@/components/ui/select-field";
import { Button } from "@/components/ui/button";
import { FormStatus } from "@/components/ui/form-status";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet } from "@/lib/api";
import { t } from "@/lib/i18n";
import {
  INFORMATION_LIST_SCROLL_CLASS,
  INFORMATION_LIST_ROW_CLASS,
} from "@/lib/list-density";
import {
  DbObjectManagementPanelShell,
  DbObjectPanelHeader,
} from "../components/DbObjectManagementShared";
import { OntologyDefinitionWorkspace } from "./OntologyDefinitionWorkspace";
import {
  DefinitionValue,
  fieldLabel,
  ontologyInputClass,
  ResultStatus,
  TechnicalDetails,
  versionOption,
} from "./ontologyResultPresentation";

export const conceptKinds = [
  "object_type",
  "property",
  "link_type",
  "function",
  "action_type",
  "interface",
  "shared_property",
  "value_type",
  "metric",
  "business_rule",
  "enumeration",
  "business_event",
  "object_set",
] as const;
type ConceptKind = (typeof conceptKinds)[number];
export interface BusinessDefinition {
  id: string;
  kind: ConceptKind;
  api_name: string;
  name_ja: string;
  description_ja: string;
  review_status: "unreviewed" | "reviewed";
  missing_information_ja: string[];
  evidence: {
    source_id: string;
    locator: string;
    excerpt_ja: string;
    verified: boolean;
  }[];
  mappings: {
    owner: string;
    object_name: string;
    column_name: string;
    expression_sql: string;
  }[];
  [key: string]: unknown;
}
export interface ProfileOntologyBundle {
  id: string;
  profile_id: string;
  display_version?: number | null;
  etag: string;
  status: "draft" | "published";
  created_at: string;
  parent_id: string;
  definitions: BusinessDefinition[];
  coverage: {
    kind: ConceptKind;
    count: number;
    status: "generated" | "insufficient_evidence" | "not_applicable" | "failed";
    reason_ja: string;
  }[];
  findings: {
    code: string;
    message_ja: string;
    definition_id: string;
    severity: string;
  }[];
  conflicts: {
    definition_id: string;
    current: BusinessDefinition;
    proposed: BusinessDefinition;
  }[];
  requires_revalidation?: boolean;
  notes_ja?: string;
  validation_report?: Record<string, unknown>;
}

export function useProfileOntologyResults(profileId: string, buildId?: string) {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: [
      "nl2sql",
      "profiles",
      "ontology-results",
      profileId,
      buildId ?? "",
    ],
    queryFn: ({ signal }) =>
      apiGet<{ results: ProfileOntologyBundle[] }>(
        `/api/nl2sql/profiles/${encodeURIComponent(profileId)}/ontology-results`,
        { signal },
      ),
    enabled: Boolean(profileId),
    retry: false,
  });
  const refresh = useCallback(async () => {
    const result = await query.refetch();
    await client.invalidateQueries({
      queryKey: ["nl2sql", "profiles", "ontology-workspace", profileId],
    });
    return result;
  }, [query.refetch, client, profileId]);
  return { ...query, refetch: refresh };
}

const baseFields = new Set([
  "id",
  "kind",
  "api_name",
  "name_ja",
  "description_ja",
  "aliases",
  "review_status",
  "origin",
  "evidence",
  "mappings",
  "missing_information_ja",
]);
export function ProfileOntologyResults({
  profileId,
  profileLabel,
  buildId,
  onTypedResult,
  onPublished,
  resultRequest,
}: {
  profileId: string;
  profileLabel?: string;
  buildId?: string;
  onTypedResult?: (hasDefinitions: boolean) => void;
  onPublished?: () => void;
  resultRequest?: { tab: "model" | "review"; sequence: number };
}) {
  const query = useProfileOntologyResults(profileId, buildId);
  const [selectedId, setSelectedId] = useWorkspaceState(
    `ontology-v2:${profileId}:selected`,
    "",
  );
  const bundle = selectedId
    ? query.data?.results.find((result) => result.id === selectedId)
    : query.data?.results[0];
  useEffect(() => {
    if (!selectedId && query.data?.results[0])
      setSelectedId(query.data.results[0].id);
  }, [query.data, selectedId, setSelectedId]);
  useEffect(() => {
    onTypedResult?.(!!query.data?.results[0]?.definitions.length);
  }, [query.data, onTypedResult]);
  return (
    <div
      id={`ontology-results-start-${profileId}`}
      tabIndex={-1}
      data-testid="ontology-typed-results"
      className="min-w-0"
    >
      <DbObjectManagementPanelShell
        id={`ontology-results-${profileId}`}
        idPrefix="ontology-results"
        role="region"
        ariaLabel={t("ontologyResults.title")}
        className="min-w-0"
      >
        <DbObjectPanelHeader
          icon={Layers}
          title={t("ontologyResults.title")}
          action={
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void query.refetch()}
              loading={query.isFetching}
            >
              {t("ontologyResults.refresh")}
            </Button>
          }
        />
        {query.data?.results.length ? (
          <SelectField
            id={`ontology-version-${profileId}`}
            label={t("ontologyResults.selectVersion")}
            value={selectedId}
            onValueChange={setSelectedId}
            options={query.data.results.map((result) => ({
              value: result.id,
              label: versionOption(result),
            }))}
          />
        ) : null}
        {selectedId && query.data && !bundle ? (
          <FormStatus
            tone="warning"
            message={t("ontologyResults.versionMissing")}
          />
        ) : null}
        {query.isPending ? (
          <LoadingState
            label={t("ontologyResults.loading")}
            operationKey={profileId}
          />
        ) : null}
        {query.isError ? (
          <ErrorState
            message={t(
              query.data ? "ontologyUi.stale" : "ontologyResults.error",
            )}
            onRetry={() => void query.refetch()}
          />
        ) : null}
        {!query.isPending && !query.isError && !bundle && !selectedId ? (
          <EmptyState
            title={t("ontologyUi.emptyResultsTitle")}
            hint={t("ontologyResults.empty")}
          />
        ) : null}
        {bundle ? (
          <>
            <details>
              <summary className="cursor-pointer text-sm">
                {t("ontologyResults.history")} ({query.data?.results.length})
              </summary>
              <ul
                className={`${INFORMATION_LIST_SCROLL_CLASS} divide-y divide-border`}
              >
                {query.data?.results.map((result) => (
                  <li key={result.id} className="py-3 text-sm">
                    {versionOption(result)}
                  </li>
                ))}
              </ul>
            </details>
            {bundle.requires_revalidation ? (
              <FormStatus
                tone="warning"
                message={t("ontologyResults.staleScope")}
              />
            ) : null}
            <OntologyDefinitionWorkspace
              resultRequest={resultRequest}
              key={bundle.id}
              bundle={bundle}
              profileId={profileId}
              profileLabel={profileLabel}
              onChanged={query.refetch}
              onPublished={onPublished}
            />
          </>
        ) : null}
      </DbObjectManagementPanelShell>
    </div>
  );
}

export function OntologyModel({
  bundle,
  profileId,
  focusId,
}: {
  bundle: ProfileOntologyBundle;
  profileId: string;
  focusId?: string;
}) {
  const prefix = `ontology-v2:${profileId}:${bundle.id}`;
  const [kind, setKind] = useWorkspaceState<ConceptKind>(
    `${prefix}:kind`,
    "object_type",
  );
  const [search, setSearch] = useWorkspaceState(`${prefix}:search`, "");
  const [selected, setSelected] = useWorkspaceState(`${prefix}:definition`, "");
  useEffect(() => {
    if (focusId) {
      const definition = bundle.definitions.find((d) => d.id === focusId);
      if (definition) {
        setKind(definition.kind);
        setSearch("");
        setSelected(focusId);
      }
    }
  }, [focusId]);
  const items = bundle.definitions.filter(
    (item) =>
      item.kind === kind &&
      `${item.name_ja} ${item.api_name} ${item.description_ja}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  const item = bundle.definitions.find((d) => d.id === selected);
  const names = Object.fromEntries(
    bundle.definitions.flatMap((d) => [
      [d.id, `${d.name_ja} (${d.api_name})`],
      [d.api_name, `${d.name_ja} (${d.api_name})`],
    ]),
  );
  return (
    <div className="grid min-w-0 gap-4">
      <div className="grid gap-4 md:grid-cols-2">
        <SelectField
          id={`${prefix}-kind`}
          label={t("ontologyResults.categories")}
          value={kind}
          onValueChange={setKind}
          options={conceptKinds.map((value) => ({
            value,
            label: `${t(`ontologyResults.kind.${value}`)} (${bundle.coverage.find((c) => c.kind === value)?.count ?? 0})`,
          }))}
        />
        <label className="grid gap-2 text-sm">
          {t("ontologyResults.search")}
          <input
            className={ontologyInputClass}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
      </div>
      <FixedSplitPane
        splitId="ontology-model"
        preferredWidePane="right"
        left={
          <div className="min-w-0">
            <h3 className="mb-2 text-sm font-semibold">
              {t("ontologyUi.list")}
            </h3>
            <ul
              className={`${INFORMATION_LIST_SCROLL_CLASS} divide-y divide-border rounded-md border border-border`}
            >
              {items.map((d) => (
                <li key={d.id}>
                  <button
                    type="button"
                    className={`${INFORMATION_LIST_ROW_CLASS} flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm hover:bg-primary/5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary ${selected === d.id ? "bg-primary/10" : ""}`}
                    aria-pressed={selected === d.id}
                    onClick={() => setSelected(d.id)}
                  >
                    <span className="min-w-0 break-words">
                      {d.name_ja}
                      <span className="block text-xs text-muted">
                        {d.api_name}
                      </span>
                    </span>
                    <ResultStatus status={d.review_status} />
                  </button>
                </li>
              ))}
            </ul>
            {!items.length ? (
              <p className="py-4 text-sm text-muted">
                {bundle.coverage.find((c) => c.kind === kind)?.reason_ja ||
                  t("ontologyResults.noMatch")}
              </p>
            ) : null}
          </div>
        }
        right={
          item ? (
            <article
              data-testid="ontology-definition-detail"
              aria-label={`${item.name_ja} (${item.api_name})`}
              className="grid min-w-0 gap-4 text-sm"
            >
              <div>
                <h3 className="font-semibold">
                  {item.name_ja} ({item.api_name})
                </h3>
                <p className="mt-1 text-muted">
                  {t(`ontologyResults.kind.${item.kind}`)}
                </p>
              </div>
              <ResultStatus status={item.review_status} />
              <p>{item.description_ja}</p>
              {item.missing_information_ja.length ? (
                <ul className="text-warning">
                  {item.missing_information_ja.map((message) => (
                    <li key={message}>{message}</li>
                  ))}
                </ul>
              ) : null}
              <h4 className="font-semibold">
                {t("ontologyResults.definition")}
              </h4>
              <dl className="grid min-w-0 gap-3">
                {Object.entries(item)
                  .filter(([key]) => !baseFields.has(key))
                  .map(([key, value]) => (
                    <div key={key} className="grid min-w-0 gap-1">
                      <dt className="text-muted">{fieldLabel(key)}</dt>
                      <dd className="min-w-0">
                        <DefinitionValue value={value} names={names} />
                      </dd>
                    </div>
                  ))}
              </dl>
              <h4 className="font-semibold">{t("ontologyResults.mapping")}</h4>
              <DefinitionValue value={item.mappings} names={names} />
              <h4 className="font-semibold">{t("ontologyResults.evidence")}</h4>
              {item.evidence.map((e, i) => (
                <blockquote key={i} className="border-l-2 border-border pl-3">
                  <p>{e.excerpt_ja}</p>
                  <p className="text-muted">
                    {e.locator} ·{" "}
                    {t(
                      e.verified
                        ? "ontologyResults.verified"
                        : "ontologyResults.unverified",
                    )}
                  </p>
                </blockquote>
              ))}
              <h4 className="font-semibold">
                {t("ontologyResults.validation")}
              </h4>
              {bundle.findings
                .filter((f) => f.definition_id === item.id)
                .map((f, i) => (
                  <p key={i}>{f.message_ja}</p>
                ))}
              <TechnicalDetails value={item} />
            </article>
          ) : (
            <p className="py-4 text-sm text-muted">
              {t(
                selected
                  ? "ontologyUi.definitionMissing"
                  : "ontologyUi.selectDefinition",
              )}
            </p>
          )
        }
      />
    </div>
  );
}
