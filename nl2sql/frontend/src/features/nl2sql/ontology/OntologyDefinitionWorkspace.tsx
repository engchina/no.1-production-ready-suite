import { ErrorState, LoadingState } from "@/components/StateViews";
import { useEffect } from "react";
import { Button } from "@/components/ui/button";
import { FormStatus } from "@/components/ui/form-status";
import { formatDateTimeWithYear } from "@/lib/format";
import { ContentActionBar } from "@/components/ContentActionBar";
import { t } from "@/lib/i18n";
import { LayoutList, Boxes, Link2, ShieldCheck, Send } from "lucide-react";
import { ManagementTabs } from "../components/DbAdminShared";
import { SelectField } from "@/components/ui/select-field";
import { FieldError } from "@/components/ui/field-error";
import { OntologyModel } from "./ProfileOntologyResults";
import {
  ChangesTable,
  DataTable,
  TechnicalDetails,
  ResultStatus,
  versionLabel,
  versionOption,
} from "./ontologyResultPresentation";
import { ProfileOntologyGraph } from "./ProfileOntologyGraph";
import { ProfileOntologyBundle } from "./ProfileOntologyResults";

import {
  tabs,
  useOntologyDefinitionWorkspace,
} from "./useOntologyDefinitionWorkspace";
export function OntologyDefinitionWorkspace({
  bundle,
  profileId,
  profileLabel,
  onChanged,
  onPublished,
  resultRequest,
}: {
  bundle: ProfileOntologyBundle;
  profileId: string;
  onChanged: () => Promise<unknown>;
  profileLabel?: string;
  onPublished?: () => void;
  resultRequest?: { tab: "model" | "review"; sequence: number };
}) {
  const {
    prefix,
    tab,
    setTab,
    focusId,
    setFocusId,
    success,
    acceptanceError,
    setAcceptanceError,
    instruction,
    setInstruction,
    notes,
    setNotes,
    acceptance,
    setAcceptance,
    busy,
    error,
    changes,
    setChanges,
    rollbackId,
    setRollbackId,
    workspace,
    job,
    readOnly,
    run,
  } = useOntologyDefinitionWorkspace({
    bundle,
    profileId,
    profileLabel,
    onChanged,
    onPublished,
  });
  useEffect(() => {
    if (resultRequest) {
      setTab(resultRequest.tab);
      requestAnimationFrame(() =>
        document.getElementById(`${prefix}-tab-${resultRequest.tab}`)?.focus(),
      );
    }
  }, [resultRequest?.sequence]);
  const unavailable =
    workspace.isError || workspace.isFetching || !workspace.data;
  const renderJson = (value: unknown) => <TechnicalDetails value={value} />;
  const dataReport = bundle.validation_report?.data_validation as
    Record<string, unknown> | undefined;
  const blockers = [
    bundle.definitions.some((d) => d.review_status !== "reviewed")
      ? t("ontologyUi.needsReview", {
          count: bundle.definitions.filter(
            (d) => d.review_status !== "reviewed",
          ).length,
        })
      : "",
    bundle.conflicts.length
      ? t("ontologyUi.needsConflict", { count: bundle.conflicts.length })
      : "",
    bundle.requires_revalidation || !bundle.validation_report?.kind
      ? t("ontologyUi.needsValidation")
      : "",
    bundle.validation_report?.errors ? t("ontologyUi.validationErrors") : "",
  ].filter(Boolean);
  return (
    <div
      className="grid min-w-0 grid-cols-1 gap-4 [overflow-wrap:anywhere]"
      data-testid="ontology-definition-workspace"
    >
      <ManagementTabs
        activeView={tab}
        tabs={tabs.map((id, i) => ({
          id,
          label: t(`ontologyWorkspace.tab.${id}`),
          icon: [LayoutList, Boxes, Link2, ShieldCheck, Send][i],
        }))}
        idPrefix={prefix}
        ariaLabel={t("ontologyWorkspace.tabs")}
        onViewChange={setTab}
      />
      {success ? <FormStatus tone="success" message={success} /> : null}
      {error ? <FormStatus tone="danger" message={error} /> : null}
      {workspace.isPending ? (
        <LoadingState
          label={t("ontologyResults.loading")}
          operationKey={bundle.id}
        />
      ) : null}
      {workspace.isError ? (
        <ErrorState
          message={t(
            workspace.data ? "ontologyUi.stale" : "ontologyWorkspace.loadError",
          )}
          onRetry={() => void workspace.refetch()}
        />
      ) : null}
      <div
        role="tabpanel"
        id={`${prefix}-panel-${tab}`}
        aria-labelledby={`${prefix}-tab-${tab}`}
        className="grid min-w-0 grid-cols-1 gap-3"
      >
        {tab === "model" ? (
          <OntologyModel
            bundle={bundle}
            profileId={profileId}
            focusId={focusId}
          />
        ) : null}
        {tab === "overview" ? (
          <>
            <p>
              {t("ontologyWorkspace.overview", {
                count: bundle.definitions.length,
                unreviewed: bundle.definitions.filter(
                  (d) => d.review_status === "unreviewed",
                ).length,
                conflicts: bundle.conflicts.length,
              })}
            </p>
            <p className="break-all text-sm">
              {t("ontologyWorkspace.version")}:{" "}
              {versionLabel(bundle.display_version)} ·{" "}
              <ResultStatus status={bundle.status} />
            </p>
            <p className="break-all text-sm">
              {t("ontologyWorkspace.published")}:{" "}
              {workspace.data?.head?.release_id
                ? versionLabel(workspace.data.head.display_version)
                : t("ontologyUi.noRelease")}
            </p>
            <p className="text-sm text-muted">
              {t("ontologyWorkspace.canonical")}
            </p>
            {workspace.data?.releases?.length ? (
              <div className="grid gap-2">
                <SelectField
                  id={`${prefix}-rollback`}
                  label={t("ontologyWorkspace.rollbackVersion")}
                  value={rollbackId}
                  onValueChange={setRollbackId}
                  options={[
                    { value: "", label: t("ontologyResults.unspecified") },
                    ...workspace.data.releases.map((r) => ({
                      value: r.id,
                      label: versionOption(r),
                    })),
                  ]}
                />
                <ContentActionBar ariaLabel={t("ontologyUi.refreshActions")}>
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={
                      !rollbackId ||
                      !!busy ||
                      rollbackId === workspace.data?.head?.release_id
                    }
                    onClick={() => void run("rollback", undefined, true)}
                  >
                    {t("ontologyWorkspace.action.rollback")}
                  </Button>
                </ContentActionBar>
              </div>
            ) : null}
            {workspace.data?.artifacts?.graph_json ? (
              <ProfileOntologyGraph
                artifact={workspace.data.artifacts.graph_json}
              />
            ) : null}
            {workspace.data?.artifacts ? (
              <details>
                <summary>{t("ontologyWorkspace.artifacts")}</summary>
                {Object.entries(workspace.data.artifacts).map(
                  ([name, content]) => (
                    <details key={name}>
                      <summary>{name}</summary>
                      <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words text-xs">
                        {content}
                      </pre>
                    </details>
                  ),
                )}
              </details>
            ) : null}
          </>
        ) : null}
        {tab === "mapping" ? (
          <DataTable
            label={t("ontologyResults.mapping")}
            headers={[
              t("ontologyResults.definition"),
              t("ontologyResults.field.owner"),
              t("ontologyResults.field.object_name" as Parameters<typeof t>[0]),
              t("ontologyResults.field.column_name" as Parameters<typeof t>[0]),
              t("ontologyCapability.expression"),
            ]}
          >
            {bundle.definitions.flatMap((d) =>
              d.mappings.map((mapping, i) => (
                <tr key={`${d.id}:${i}`}>
                  <td>
                    <ContentActionBar
                      ariaLabel={t("ontologyUi.refreshActions")}
                    >
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          setFocusId(d.id);
                          setTab("model");
                        }}
                      >
                        {d.name_ja} ({d.api_name})
                      </Button>
                    </ContentActionBar>
                  </td>
                  <td>{mapping.owner}</td>
                  <td>{mapping.object_name}</td>
                  <td>{mapping.column_name}</td>
                  <td>{mapping.expression_sql}</td>
                </tr>
              )),
            )}
          </DataTable>
        ) : null}
        {tab === "validation" ? (
          <>
            <h3 className="font-semibold">
              {t("ontologyUi.staticValidation")}
            </h3>
            <p>
              {bundle.validation_report?.kind
                ? t("ontologyUi.validationSummary", {
                    count: bundle.definitions.length,
                    errors: Number(bundle.validation_report.errors ?? 0),
                    warnings: bundle.findings.filter(
                      (f) => f.severity !== "error",
                    ).length,
                  })
                : t("ontologyUi.notRun")}
            </p>
            <ContentActionBar ariaLabel={t("ontologyUi.refreshActions")}>
              <Button
                size="lg"
                variant="secondary"
                disabled={unavailable || readOnly || !!busy}
                loading={busy === "validate"}
                onClick={() => void run("validate")}
              >
                {t("ontologyWorkspace.action.validate")}
              </Button>
            </ContentActionBar>
            {bundle.findings.length ? (
              <ul className="grid gap-2">
                {bundle.findings.map((finding, index) => (
                  <li
                    key={index}
                    className="rounded border border-border p-2 text-sm"
                  >
                    <strong>
                      {finding.severity === "error"
                        ? t("ontologyWorkspace.blocker")
                        : t("ontologyWorkspace.warning")}
                    </strong>{" "}
                    · {finding.message_ja}
                    <p className="break-all text-muted">
                      {bundle.definitions.find(
                        (d) => d.id === finding.definition_id,
                      )?.name_ja ?? finding.definition_id}{" "}
                      · {finding.code}
                    </p>
                  </li>
                ))}
              </ul>
            ) : (
              <p>{t("ontologyWorkspace.noFindings")}</p>
            )}
            {bundle.validation_report?.checked_at ? (
              <p className="text-sm text-muted">
                {t("ontologyUi.checkedAt")}:{" "}
                {formatDateTimeWithYear(
                  String(bundle.validation_report.checked_at),
                )}
              </p>
            ) : null}
            {bundle.validation_report
              ? renderJson(bundle.validation_report)
              : null}
            <h3 className="mt-4 font-semibold">
              {t("ontologyUi.dataValidation")}
            </h3>
            <p>
              {job.data ? (
                <ResultStatus status={job.data.status} />
              ) : !!dataReport ? (
                t("ontologyUi.success")
              ) : (
                t("ontologyUi.notRun")
              )}
            </p>
            {dataReport ? (
              <div className="grid gap-2 text-sm">
                <p>
                  {t("ontologyUi.checkedAt")}:{" "}
                  {formatDateTimeWithYear(String(dataReport.checked_at ?? ""))}
                </p>
                <p>
                  {t("ontologyUi.dataSummary", {
                    count: Number(dataReport.instance_count ?? 0),
                  })}{" "}
                  · {t("ontologyUi.errors")}: {Number(dataReport.errors ?? 0)}
                </p>
                <p>
                  {t("ontologyUi.sampleScope", {
                    limit: Number(dataReport.sample_limit ?? 0),
                    coverage: Math.round(
                      Number(dataReport.instance_coverage ?? 0) * 100,
                    ),
                  })}
                </p>
                {Array.isArray(dataReport.skipped_targets) ? (
                  <ul className="grid gap-2">
                    {dataReport.skipped_targets.map((item, index) => (
                      <li key={index} className="text-warning">
                        {bundle.definitions.find((d) => d.id === item.target)
                          ?.name_ja ?? t("ontologyResults.unspecified")}{" "}
                        · {item.reason_ja}
                      </li>
                    ))}
                  </ul>
                ) : null}
                {Array.isArray(dataReport.acceptance_cases) ? (
                  <ul className="grid gap-2">
                    {dataReport.acceptance_cases.map((item, index) => (
                      <li key={index} className="flex gap-2">
                        <ResultStatus
                          status={item.passed ? "succeeded" : "failed"}
                        />
                        <span>{item.question_ja}</span>
                      </li>
                    ))}
                  </ul>
                ) : null}
                <TechnicalDetails value={dataReport} />
              </div>
            ) : null}
            <details open={!!acceptanceError || undefined}>
              <summary>{t("ontologyWorkspace.acceptance")}</summary>
              <p className="text-sm">{t("ontologyWorkspace.acceptanceHint")}</p>
              <label className="grid gap-1">
                {t("ontologyWorkspace.acceptanceInput")}
                <textarea
                  className="min-h-28 w-full rounded border border-border bg-background p-2"
                  aria-describedby={
                    acceptanceError ? `${prefix}-acceptance-error` : undefined
                  }
                  aria-invalid={!!acceptanceError}
                  value={acceptance}
                  onChange={(e) => {
                    setAcceptance(e.target.value);
                    setAcceptanceError("");
                  }}
                />
              </label>
              {acceptanceError ? (
                <FieldError
                  id={`${prefix}-acceptance-error`}
                  message={acceptanceError}
                />
              ) : null}
            </details>
            <ContentActionBar ariaLabel={t("ontologyUi.refreshActions")}>
              <Button
                size="lg"
                variant="secondary"
                disabled={
                  unavailable ||
                  readOnly ||
                  !!busy ||
                  (!!job.data &&
                    ["queued", "claimed", "running"].includes(job.data.status))
                }
                onClick={() => void run("data", undefined, true)}
              >
                {t("ontologyWorkspace.action.data")}
              </Button>
            </ContentActionBar>
            {job.data ? (
              <p role="status">
                {t("ontologyWorkspace.dataJob")}:{" "}
                {t(
                  `ontologyWorkspace.job.${job.data.status}` as Parameters<
                    typeof t
                  >[0],
                )}
              </p>
            ) : null}
            {job.data?.error_message_ja ? (
              <FormStatus tone="danger" message={job.data.error_message_ja} />
            ) : null}
            {job.isError ? (
              <FormStatus
                tone="danger"
                message={t("ontologyWorkspace.loadError")}
              />
            ) : null}
          </>
        ) : null}
        {tab === "review" ? (
          <>
            <label className="grid gap-1">
              {t("ontologyWorkspace.instruction")}
              <textarea
                value={instruction}
                onChange={(e) => {
                  setInstruction(e.target.value);
                  setChanges(null);
                }}
                className="min-h-28 w-full rounded border border-border bg-background p-2"
                disabled={unavailable || readOnly || !!busy}
              />
            </label>
            <ContentActionBar ariaLabel={t("ontologyUi.refreshActions")}>
              <Button
                size="lg"
                variant="secondary"
                disabled={
                  unavailable || readOnly || !!busy || !instruction.trim()
                }
                loading={busy === "analyze"}
                onClick={() => void run("analyze")}
              >
                {t("ontologyWorkspace.action.analyze")}
              </Button>
            </ContentActionBar>
            {changes ? (
              <div className="grid min-w-0 gap-3">
                <ChangesTable before={changes.before} after={changes.after} />
                <TechnicalDetails value={changes} />
                <ContentActionBar ariaLabel={t("ontologyUi.refreshActions")}>
                  <Button
                    size="lg"
                    variant="secondary"
                    disabled={
                      !!busy ||
                      changes.base_etag.replaceAll('"', "") !== bundle.etag
                    }
                    onClick={() => void run("apply", { confirmed: true }, true)}
                  >
                    {t("ontologyWorkspace.action.apply")}
                  </Button>
                </ContentActionBar>
              </div>
            ) : null}
            {bundle.conflicts.map((conflict, index) => (
              <div
                key={index}
                className="grid min-w-0 gap-2 rounded border border-border p-3"
              >
                <p>
                  {conflict.current.name_ja} → {conflict.proposed.name_ja}
                </p>
                <div className="flex flex-wrap gap-2">
                  {(["current", "proposed"] as const).map((choice) => (
                    <ContentActionBar
                      key={choice}
                      ariaLabel={t("ontologyUi.refreshActions")}
                    >
                      <Button
                        size="sm"
                        variant="secondary"
                        disabled={unavailable || readOnly || !!busy}
                        onClick={() =>
                          void run(`conflicts/${index}/resolve`, { choice })
                        }
                      >
                        {t(`ontologyResults.${choice}`)}
                      </Button>
                    </ContentActionBar>
                  ))}
                </div>
              </div>
            ))}
            <label className="grid gap-1">
              {t("ontologyWorkspace.notes")}
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                className="min-h-20 rounded border border-border bg-background p-2"
                disabled={unavailable || readOnly || !!busy}
              />
            </label>
            <ContentActionBar ariaLabel={t("ontologyUi.refreshActions")}>
              <Button
                size="sm"
                variant="secondary"
                disabled={
                  unavailable ||
                  readOnly ||
                  !!busy ||
                  notes === (bundle.notes_ja ?? "")
                }
                onClick={() => void run("notes", { notes_ja: notes })}
              >
                {t("ontologyWorkspace.action.notes")}
              </Button>
            </ContentActionBar>
            <ContentActionBar ariaLabel={t("ontologyUi.refreshActions")}>
              <Button
                size="lg"
                variant="secondary"
                disabled={
                  unavailable || readOnly || !!busy || !!bundle.conflicts.length
                }
                onClick={() =>
                  void run(
                    "review",
                    {
                      definition_ids: bundle.definitions.map((d) => d.id),
                      confirmed: true,
                    },
                    true,
                  )
                }
              >
                {t("ontologyWorkspace.action.review")}
              </Button>
            </ContentActionBar>
            <ContentActionBar ariaLabel={t("ontologyUi.refreshActions")}>
              <Button
                size="lg"
                variant="secondary"
                disabled={unavailable || readOnly || !!busy}
                onClick={() => void run("validate")}
              >
                {t("ontologyWorkspace.action.validate")}
              </Button>
            </ContentActionBar>
            <div className="grid gap-2 rounded-md bg-background p-4">
              <h3 className="font-semibold">
                {t("ontologyUi.publishRequirements")}
              </h3>
              {readOnly ? (
                <p>{t("ontologyUi.publishedReadOnly")}</p>
              ) : blockers.length ? (
                <ul className="grid gap-2">
                  {blockers.map((message) => (
                    <li key={message} className="text-sm text-warning">
                      {message}
                    </li>
                  ))}
                </ul>
              ) : (
                <p>{t("ontologyUi.readyToPublish")}</p>
              )}
              <ContentActionBar ariaLabel={t("ontologyUi.refreshActions")}>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setTab("validation")}
                >
                  {t("ontologyUi.staticValidation")}
                </Button>
              </ContentActionBar>
            </div>
            <ContentActionBar ariaLabel={t("ontologyUi.refreshActions")}>
              <Button
                size="lg"
                variant="primary"
                disabled={
                  unavailable ||
                  readOnly ||
                  !!busy ||
                  workspace.isError ||
                  !workspace.data ||
                  bundle.definitions.some(
                    (d) => d.review_status !== "reviewed",
                  ) ||
                  !!bundle.conflicts.length ||
                  !!bundle.requires_revalidation ||
                  !bundle.validation_report?.kind ||
                  !!bundle.validation_report.errors
                }
                loading={busy === "publish"}
                onClick={() =>
                  void run(
                    "publish",
                    {
                      expected_head: workspace.data?.head?.release_id ?? "",
                      confirmed: true,
                    },
                    true,
                  )
                }
              >
                {t("ontologyWorkspace.action.publish")}
              </Button>
            </ContentActionBar>
            <p className="text-sm text-muted">
              {t("ontologyWorkspace.publishHint")}
            </p>
          </>
        ) : null}
      </div>
    </div>
  );
}
