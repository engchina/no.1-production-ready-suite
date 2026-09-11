import { EmptyState, LoadingState, ErrorState } from "@/components/StateViews";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { FormStatus } from "@/components/ui/form-status";
import { useWorkspaceState } from "@/components/WorkspaceState";
import { apiGet } from "@/lib/api";
import { SelectField } from "@/components/ui/select-field";
import { FieldError } from "@/components/ui/field-error";
import { FieldLabel } from "@/components/ui/required-field";
import { formatDateTimeWithYear } from "@/lib/format";
import { Zap } from "lucide-react";
import { FixedSplitPane } from "@/components/layout/FixedSplitPane";
import {
  DbObjectManagementPanelShell,
  DbObjectPanelHeader,
} from "../components/DbObjectManagementShared";
import { ContentActionBar } from "@/components/ContentActionBar";
import {
  INFORMATION_LIST_SCROLL_CLASS,
  INFORMATION_LIST_ROW_CLASS,
} from "@/lib/list-density";
import {
  DefinitionValue,
  EffectTable,
  TechnicalDetails,
  ResultStatus,
  versionLabel,
  ontologyInputClass,
} from "./ontologyResultPresentation";
import { t } from "@/lib/i18n";

import {
  useOntologyCapability,
  type Parameter,
  type Capability,
  type Catalog,
} from "./useOntologyCapability";

export function OntologyCapabilities({
  profileId,
  profileLabel,
  onOpenResults,
}: {
  profileId: string;
  profileLabel?: string;
  onOpenResults?: (tab: "model" | "review") => void;
}) {
  const endpoint = `/api/nl2sql/profiles/${encodeURIComponent(profileId)}/ontology-capabilities`;
  const query = useQuery({
    queryKey: ["nl2sql", "profiles", "ontology-capabilities", profileId],
    queryFn: ({ signal }) => apiGet<Catalog>(endpoint, { signal }),
    retry: false,
  });
  const [selected, setSelected] = useWorkspaceState(
    `ontology-capability:${profileId}:selected`,
    "",
  );
  const capability = query.data?.capabilities.find(
    (c) => c.definition.id === selected,
  );
  return (
    <DbObjectManagementPanelShell
      id={`ontology-capabilities-${profileId}`}
      idPrefix="ontology-capabilities"
      role="region"
      ariaLabel={t("ontologyCapability.title")}
      className="min-w-0"
    >
      <DbObjectPanelHeader
        icon={Zap}
        title={t("ontologyCapability.title")}
        description={t("ontologyUi.capabilityScope")}
        action={
          <ContentActionBar ariaLabel={t("ontologyUi.capabilityActions")}>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => void query.refetch()}
              loading={query.isFetching}
            >
              {t("ontologyCapability.refresh")}
            </Button>
          </ContentActionBar>
        }
      />
      {query.isPending ? (
        <LoadingState
          label={t("ontologyCapability.loading")}
          operationKey={profileId}
        />
      ) : null}
      {query.isError ? (
        <ErrorState
          message={t(
            query.data ? "ontologyUi.stale" : "ontologyWorkspace.loadError",
          )}
          onRetry={() => void query.refetch()}
        />
      ) : null}
      {query.data ? (
        <>
          <p className="text-sm">
            {t("ontologyWorkspace.published")}:{" "}
            {query.data.release_id
              ? versionLabel(query.data.display_version)
              : t("ontologyUi.noRelease")}
          </p>
          {query.data.capabilities.length ? (
            <FixedSplitPane
              splitId="ontology-capabilities"
              preferredWidePane="right"
              left={
                <div className="min-w-0">
                  <h3 className="mb-2 text-sm font-semibold">
                    {t("ontologyUi.capabilityList")}
                  </h3>
                  <ul
                    className={`${INFORMATION_LIST_SCROLL_CLASS} divide-y divide-border rounded-md border border-border`}
                  >
                    {query.data.capabilities.map((c) => (
                      <li key={c.definition.id}>
                        <button
                          type="button"
                          className={`${INFORMATION_LIST_ROW_CLASS} flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm hover:bg-primary/5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary ${selected === c.definition.id ? "bg-primary/10" : ""}`}
                          aria-pressed={selected === c.definition.id}
                          onClick={() => setSelected(c.definition.id)}
                        >
                          <span>
                            {c.definition.name_ja}
                            <span className="block text-xs text-muted">
                              {t(
                                c.definition.kind === "function"
                                  ? "ontologyResults.kind.function"
                                  : "ontologyResults.kind.action_type",
                              )}
                            </span>
                          </span>
                          <ResultStatus status={c.status} />
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              }
              right={
                capability ? (
                  <CapabilityCard
                    key={`${profileId}:${query.data.release_id}:${capability.definition.id}`}
                    profileId={profileId}
                    profileLabel={profileLabel}
                    releaseId={query.data.release_id}
                    displayVersion={query.data.display_version}
                    capability={capability}
                    implementations={
                      capability.definition.kind === "function"
                        ? query.data.implementations.functions
                        : query.data.implementations.actions
                    }
                    refresh={() => query.refetch()}
                    stale={query.isError || query.isFetching}
                  />
                ) : (
                  <p className="py-4 text-sm text-muted">
                    {t(
                      selected
                        ? "ontologyUi.capabilityMissing"
                        : "ontologyUi.selectCapability",
                    )}
                  </p>
                )
              }
            />
          ) : (
            <div className="grid gap-2 py-4">
              <EmptyState
                title={t(
                  query.data.release_id
                    ? "ontologyCapability.empty"
                    : "ontologyUi.noRelease",
                )}
                hint={t(
                  query.data.release_id
                    ? "ontologyUi.emptyCapabilitiesHint"
                    : "ontologyUi.noReleaseHint",
                )}
              />
              <ContentActionBar ariaLabel={t("ontologyUi.capabilityActions")}>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() =>
                    onOpenResults?.(query.data?.release_id ? "model" : "review")
                  }
                >
                  {t(
                    query.data.release_id
                      ? "ontologyUi.goModel"
                      : "ontologyUi.goReview",
                  )}
                </Button>
              </ContentActionBar>
            </div>
          )}
        </>
      ) : null}
    </DbObjectManagementPanelShell>
  );
}

function CapabilityCard({
  profileId,
  profileLabel,
  releaseId,
  displayVersion,
  capability,
  implementations,
  refresh,
  stale,
}: {
  profileId: string;
  profileLabel?: string;
  releaseId: string;
  displayVersion?: number | null;
  capability: Capability;
  implementations: string[];
  refresh: () => Promise<unknown>;
  stale: boolean;
}) {
  const {
    definition,
    isFunction,
    values,
    setValues,
    targets,
    setTargets,
    kind,
    setKind,
    sql,
    setSql,
    implementation,
    setImplementation,
    rules,
    setRules,
    states,
    setStates,
    enabled,
    setEnabled,
    lastId,
    lastInput,
    pending,
    preview,
    setPreview,
    busy,
    error,
    success,
    fieldErrors,
    setFieldErrors,
    prefix,
    input,
    execution,
    outcome,
    run,
  } = useOntologyCapability({
    profileId,
    profileLabel,
    releaseId,
    displayVersion,
    capability,
    implementations,
    refresh,
    stale,
  });
  const fields = (
    group: string,
    parameters: Parameter[],
    entries: Record<string, string>,
    update: (next: Record<string, string>) => void,
  ) =>
    parameters.map((parameter) => {
      const key = `${group}:${parameter.api_name}`,
        id = `${prefix}:${key}`,
        error = fieldErrors[key];
      const change = (value: string) => {
        setPreview(null);
        setFieldErrors({ ...fieldErrors, [key]: "" });
        update({ ...entries, [parameter.api_name]: value });
      };
      return (
        <div key={key} className="grid min-w-0 gap-2 text-sm">
          {parameter.data_type === "boolean" ? (
            <SelectField
              id={id}
              label={`${parameter.name_ja} (${parameter.api_name})`}
              required={parameter.required}
              value={entries[parameter.api_name] ?? ""}
              onValueChange={change}
              error={error}
              options={[
                { value: "", label: t("ontologyResults.unspecified") },
                { value: "true", label: t("ontologyResults.yes") },
                { value: "false", label: t("ontologyResults.no") },
              ]}
            />
          ) : (
            <>
              <FieldLabel
                htmlFor={id}
                required={parameter.required}
                label={`${parameter.name_ja} (${parameter.api_name})`}
              />
              <input
                id={id}
                required={parameter.required}
                aria-invalid={!!error}
                aria-describedby={error ? `${id}-error` : undefined}
                value={entries[parameter.api_name] ?? ""}
                onChange={(e) => change(e.target.value)}
                type={parameter.data_type === "date" ? "date" : "text"}
                inputMode={
                  ["integer", "number"].includes(parameter.data_type)
                    ? "decimal"
                    : "text"
                }
                className={ontologyInputClass}
              />
              <FieldError id={`${id}-error`} message={error} />
            </>
          )}
        </div>
      );
    });
  return (
    <article
      className="grid min-w-0 grid-cols-1 gap-4"
      aria-label={`${definition.name_ja} (${definition.api_name})`}
    >
      <h4 className="font-medium">
        {definition.name_ja} ({definition.api_name})
      </h4>
      <ResultStatus status={capability.status} />
      <p className="text-sm text-muted">{definition.description_ja}</p>
      {capability.reason_ja ? (
        <p className="text-sm text-muted">{capability.reason_ja}</p>
      ) : null}
      {success ? <FormStatus tone="success" message={success} /> : null}
      {error ? <FormStatus tone="danger" message={error} /> : null}
      <details className="min-w-0" open={!capability.binding || undefined}>
        <summary>{t("ontologyCapability.binding")}</summary>
        <div className="grid min-w-0 grid-cols-1 gap-3 py-3">
          <SelectField
            id={`${prefix}-binding-kind`}
            label={t("ontologyCapability.kind")}
            value={kind}
            onValueChange={setKind}
            options={(isFunction
              ? ["expression", "sql", "backend"]
              : ["property_update", "backend"]
            ).map((value) => ({
              value,
              label: t(
                `ontologyCapability.kind.${value}` as Parameters<typeof t>[0],
              ),
            }))}
          />
          {kind === "backend" ? (
            <SelectField
              id={`${prefix}-implementation`}
              label={t("ontologyCapability.implementation")}
              value={implementation}
              onValueChange={setImplementation}
              required
              error={fieldErrors.implementation}
              options={[
                { value: "", label: t("ontologyResults.unspecified") },
                ...implementations.map((value) => ({ value, label: value })),
              ]}
            />
          ) : isFunction ? (
            <label className="grid gap-1">
              {t("ontologyCapability.expression")}
              <textarea
                value={sql}
                onChange={(e) => setSql(e.target.value)}
                aria-invalid={!!fieldErrors.sql}
                aria-describedby={`${prefix}-sql-error`}
                className="min-h-24 min-w-0 rounded border border-border bg-background p-2 font-mono text-xs"
              />
              <FieldError
                id={`${prefix}-sql-error`}
                message={fieldErrors.sql}
              />
            </label>
          ) : null}
          {!isFunction ? (
            <>
              <label className="grid gap-1">
                {t("ontologyCapability.rules")}
                <textarea
                  value={rules}
                  onChange={(e) => setRules(e.target.value)}
                  className="min-h-20 min-w-0 rounded border border-border bg-background p-2"
                />
              </label>
              <p className="text-xs text-muted">
                {t("ontologyCapability.statesHint")}
              </p>
              <label className="grid gap-1">
                {t("ontologyCapability.states")}
                <textarea
                  aria-invalid={!!fieldErrors.states}
                  aria-describedby={`${prefix}-states-error`}
                  value={states}
                  onChange={(e) => setStates(e.target.value)}
                  className="min-h-20 min-w-0 rounded border border-border bg-background p-2 font-mono text-xs"
                />
                <FieldError
                  id={`${prefix}-states-error`}
                  message={fieldErrors.states}
                />
              </label>
            </>
          ) : null}
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => {
                setEnabled(e.target.checked);
                setPreview(null);
              }}
            />
            {t("ontologyCapability.enabled")}
          </label>
          <ContentActionBar ariaLabel={t("ontologyUi.capabilityActions")}>
            <Button
              size="lg"
              variant="secondary"
              disabled={!!busy || stale}
              onClick={() => void run("bind")}
            >
              <span className="block w-full whitespace-normal break-words">
                {t("ontologyCapability.bind")}
              </span>
            </Button>
          </ContentActionBar>
        </div>
      </details>
      {capability.status === "available" ? (
        <>
          <fieldset className="grid min-w-0 gap-3">
            <legend className="mb-2 text-sm font-medium">
              {t("ontologyCapability.parameters")}
            </legend>
            {fields("values", definition.parameters ?? [], values, setValues)}
            {fields(
              "targets",
              capability.target_parameters ?? [],
              targets,
              setTargets,
            )}
          </fieldset>
          <ContentActionBar ariaLabel={t("ontologyUi.capabilityActions")}>
            <Button
              size="lg"
              variant="secondary"
              disabled={!!busy || stale || !!pending}
              loading={busy === "preview" || busy === "invoke"}
              onClick={() => void run(isFunction ? "invoke" : "preview")}
            >
              <span className="block w-full whitespace-normal break-words">
                {t(
                  isFunction
                    ? "ontologyCapability.invoke"
                    : "ontologyCapability.preview",
                )}
              </span>
            </Button>
          </ContentActionBar>
        </>
      ) : null}
      {pending ? (
        <div className="grid min-w-0 gap-3" role="status">
          <p className="text-sm">{t("ontologyCapability.pending")}</p>
          <p className="break-all text-xs">
            {t("ontologyWorkspace.published")}:{" "}
            {versionLabel(pending.displayVersion)}
          </p>
          <EffectTable
            before={pending.preview.before}
            after={pending.preview.after}
          />
          {outcome.isError ? (
            <FormStatus
              tone="danger"
              message={t("ontologyWorkspace.loadError")}
            />
          ) : null}
          <ContentActionBar ariaLabel={t("ontologyUi.capabilityActions")}>
            <Button
              size="lg"
              variant="secondary"
              disabled={!!busy}
              loading={outcome.isFetching}
              onClick={() => void outcome.refetch()}
            >
              <span className="block w-full whitespace-normal break-words">
                {t("ontologyCapability.checkOutcome")}
              </span>
            </Button>
          </ContentActionBar>
          <ContentActionBar ariaLabel={t("ontologyUi.capabilityActions")}>
            <Button
              size="lg"
              variant="primary"
              disabled={
                !!busy ||
                outcome.isFetching ||
                outcome.isError ||
                outcome.data?.status !== "unresolved"
              }
              onClick={() => void run("execute")}
            >
              <span className="block w-full whitespace-normal break-words">
                {t("ontologyCapability.retryOriginal")}
              </span>
            </Button>
          </ContentActionBar>
        </div>
      ) : null}
      {preview && !pending ? (
        <div className="grid min-w-0 gap-3">
          <p className="text-xs">
            {t("ontologyCapability.expires")}:{" "}
            {formatDateTimeWithYear(preview.expires_at)}
          </p>
          <EffectTable before={preview.before} after={preview.after} />
          <ContentActionBar ariaLabel={t("ontologyUi.capabilityActions")}>
            <Button
              size="lg"
              variant="primary"
              disabled={!!busy || stale}
              loading={busy === "execute"}
              onClick={() => void run("execute")}
            >
              <span className="block w-full whitespace-normal break-words">
                {t("ontologyCapability.execute")}
              </span>
            </Button>
          </ContentActionBar>
        </div>
      ) : null}
      {lastId ? (
        <div className="grid min-w-0 gap-2">
          <p className="text-sm">
            {t("ontologyCapability.previous")}:{" "}
            {formatDateTimeWithYear(execution.data?.at)}
          </p>
          {lastInput !== input ? (
            <p className="text-sm text-muted">
              {t("ontologyCapability.unexecuted")}
            </p>
          ) : null}
          {execution.isError ? (
            <FormStatus
              tone="danger"
              message={t("ontologyWorkspace.loadError")}
            />
          ) : null}
          {execution.data ? (
            <>
              <p>
                {t("ontologyUi.previousVersion")}:{" "}
                {versionLabel(execution.data.display_version)}
              </p>
              <ResultStatus status={execution.data.status} />
              {execution.data.message_ja ? (
                <p className="text-sm">{execution.data.message_ja}</p>
              ) : null}
              {execution.data.before || execution.data.after ? (
                <EffectTable
                  before={execution.data.before}
                  after={execution.data.after}
                />
              ) : (
                <DefinitionValue value={execution.data.result} />
              )}
              <TechnicalDetails value={execution.data} />
            </>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}
