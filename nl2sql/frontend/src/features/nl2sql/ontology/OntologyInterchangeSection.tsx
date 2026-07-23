import { useEffect, useMemo, useState } from "react";
import { ArrowLeftRight, Download, LayoutTemplate } from "lucide-react";

import { Banner, Button, EmptyState, toast } from "@engchina/production-ready-ui";

import { ErrorState } from "@/components/StateViews";
import { t } from "@/lib/i18n";
import { downloadBlob, downloadFilename } from "@/lib/download";
import { FileInputControl } from "../components/DbAdminShared";
import {
  DbManagementLoadingSkeleton,
  DbObjectManagementPanelShell,
  DbObjectPanelHeader,
} from "../components/DbObjectManagementShared";
import {
  applyOntologyTemplate,
  downloadOntologyExport,
  importOntologyRdf,
  listOntologyRevisions,
  listOntologyTemplates,
  type OntologyInterchangeApplyData,
  type OntologyTemplateSummary,
} from "./api";
import type { OntologyRevision } from "./types";

export interface OntologyInterchangeSectionProps {
  profileId: string;
  /** 提案の登録に成功したとき(テンプレート適用 / RDF import)。 */
  onProposalsRegistered?: () => void;
}

const selectClass =
  "min-h-11 min-w-0 rounded-md border border-border bg-card px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring/40";
const inputClass =
  "min-h-11 min-w-0 rounded-md border border-border bg-card px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring/40";

/** dry-run / 適用 / import の共通結果表示。 */
function InterchangeResult({
  result,
  registered,
}: {
  result: OntologyInterchangeApplyData;
  registered: boolean;
}) {
  return (
    <div className="grid gap-2" data-testid="ontology-interchange-result" aria-live="polite">
      <p className="text-sm text-foreground">
        {registered
          ? t("ontologyInterchange.result.registered", {
              count: result.proposal_ids.length,
            })
          : t("ontologyInterchange.result.preview", { count: result.proposal_count })}
      </p>
      {result.resolved.length > 0 ? (
        <p className="text-sm text-muted">
          {t("ontologyInterchange.result.resolved")}:{" "}
          {result.resolved.map((item) => `${item.key} → ${item.object_name}`).join("、")}
        </p>
      ) : null}
      {result.unresolved.length > 0 ? (
        <p className="text-sm text-muted">
          {t("ontologyInterchange.result.unresolved")}: {result.unresolved.join("、")}
          ({t("ontologyInterchange.result.termFallback", {
            count: result.term_proposal_count,
          })})
        </p>
      ) : null}
      {result.warnings_ja.length > 0 ? (
        <Banner severity="warning">
          <ul className="grid list-disc gap-1 pl-4">
            {result.warnings_ja.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </Banner>
      ) : null}
    </div>
  );
}

export function OntologyInterchangeSection({
  profileId,
  onProposalsRegistered,
}: OntologyInterchangeSectionProps) {
  const [templates, setTemplates] = useState<OntologyTemplateSummary[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(true);
  const [templatesError, setTemplatesError] = useState("");
  const [selectedTemplateId, setSelectedTemplateId] = useState("");
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [applyResult, setApplyResult] = useState<OntologyInterchangeApplyData | null>(null);
  const [applyRegistered, setApplyRegistered] = useState(false);
  const [applyBusy, setApplyBusy] = useState<"" | "preview" | "apply">("");
  const [applyError, setApplyError] = useState("");

  const [revisions, setRevisions] = useState<OntologyRevision[]>([]);
  const [exportRevisionId, setExportRevisionId] = useState("");
  const [exportFormat, setExportFormat] = useState<"rdfxml" | "turtle">("rdfxml");
  const [exportBusy, setExportBusy] = useState(false);

  const [importFile, setImportFile] = useState<File | null>(null);
  const [importBusy, setImportBusy] = useState(false);
  const [importError, setImportError] = useState("");
  const [importResult, setImportResult] = useState<OntologyInterchangeApplyData | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setTemplatesLoading(true);
    setTemplatesError("");
    Promise.all([
      listOntologyTemplates({ signal: controller.signal }),
      listOntologyRevisions({ signal: controller.signal }),
    ])
      .then(([templateItems, revisionData]) => {
        setTemplates(templateItems);
        setRevisions(revisionData.revisions ?? []);
        setExportRevisionId(
          revisionData.active_revision_id || revisionData.revisions?.[0]?.id || ""
        );
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setTemplatesError(
          err instanceof Error ? err.message : t("ontologyInterchange.templates.error")
        );
      })
      .finally(() => {
        if (!controller.signal.aborted) setTemplatesLoading(false);
      });
    return () => controller.abort();
  }, []);

  // プロファイル切替時は適用/import の途中状態を破棄する
  useEffect(() => {
    setApplyResult(null);
    setApplyRegistered(false);
    setApplyError("");
    setOverrides({});
    setImportResult(null);
    setImportError("");
    setImportFile(null);
  }, [profileId]);

  const selectedTemplate = useMemo(
    () => templates.find((template) => template.id === selectedTemplateId) ?? null,
    [templates, selectedTemplateId]
  );

  const runTemplate = async (dryRun: boolean) => {
    if (!selectedTemplateId) return;
    setApplyBusy(dryRun ? "preview" : "apply");
    setApplyError("");
    try {
      const cleanedOverrides = Object.fromEntries(
        Object.entries(overrides).filter(([, value]) => value.trim() !== "")
      );
      const result = await applyOntologyTemplate(profileId, selectedTemplateId, {
        overrides: cleanedOverrides,
        dry_run: dryRun,
      });
      setApplyResult(result);
      setApplyRegistered(!dryRun);
      if (!dryRun) {
        toast.success(
          t("ontologyInterchange.templates.applySuccess", {
            count: result.proposal_ids.length,
          })
        );
        onProposalsRegistered?.();
      }
    } catch (err) {
      setApplyError(
        err instanceof Error ? err.message : t("ontologyInterchange.templates.applyError")
      );
    } finally {
      setApplyBusy("");
    }
  };

  const runExport = async () => {
    if (!exportRevisionId) return;
    setExportBusy(true);
    try {
      const { blob, response } = await downloadOntologyExport(exportRevisionId, exportFormat);
      const extension = exportFormat === "rdfxml" ? "rdf" : "ttl";
      downloadBlob(
        downloadFilename(response, `ontology-${exportRevisionId}.${extension}`),
        blob
      );
      toast.success(t("ontologyInterchange.export.success"));
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : t("ontologyInterchange.export.error")
      );
    } finally {
      setExportBusy(false);
    }
  };

  const runImport = async () => {
    if (!importFile) return;
    setImportBusy(true);
    setImportError("");
    try {
      const result = await importOntologyRdf(profileId, importFile);
      setImportResult(result);
      toast.success(
        t("ontologyInterchange.import.success", { count: result.proposal_ids.length })
      );
      onProposalsRegistered?.();
    } catch (err) {
      setImportError(
        err instanceof Error ? err.message : t("ontologyInterchange.import.error")
      );
    } finally {
      setImportBusy(false);
    }
  };

  return (
    <DbObjectManagementPanelShell
      id="ontology-interchange-panel"
      role="region"
      ariaLabel={t("ontologyInterchange.title")}
      idPrefix="ontology-interchange"
    >
      <DbObjectPanelHeader
        icon={ArrowLeftRight}
        title={t("ontologyInterchange.title")}
        description={t("ontologyInterchange.description")}
      />

      {templatesLoading ? (
        <DbManagementLoadingSkeleton
          idPrefix="ontology-interchange"
          ariaLabel={t("ontologyInterchange.templates.loading")}
          variant="compact"
        />
      ) : templatesError ? (
        <ErrorState message={templatesError} onRetry={() => window.location.reload()} />
      ) : (
        <div className="grid gap-6">
          {/* --- 業種テンプレートカタログ --- */}
          <section className="grid gap-3" aria-label={t("ontologyInterchange.templates.title")}>
            <div className="flex items-center gap-2">
              <LayoutTemplate size={16} aria-hidden="true" className="text-muted" />
              <h3 className="text-sm font-semibold text-foreground">
                {t("ontologyInterchange.templates.title")}
              </h3>
            </div>
            {templates.length === 0 ? (
              <EmptyState
                title={t("ontologyInterchange.templates.emptyTitle")}
                hint={t("ontologyInterchange.templates.emptyHint")}
              />
            ) : (
              <div
                className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3"
                role="radiogroup"
                aria-label={t("ontologyInterchange.templates.selectLabel")}
              >
                {templates.map((template) => {
                  const selected = template.id === selectedTemplateId;
                  return (
                    <button
                      key={template.id}
                      type="button"
                      role="radio"
                      aria-checked={selected}
                      data-testid={`ontology-template-${template.id}`}
                      onClick={() => {
                        setSelectedTemplateId(template.id);
                        setApplyResult(null);
                        setApplyRegistered(false);
                        setApplyError("");
                        setOverrides({});
                      }}
                      className={`grid min-h-11 cursor-pointer gap-1 rounded-md border p-3 text-left transition-colors focus:outline-none focus:ring-2 focus:ring-ring/40 ${
                        selected
                          ? "border-primary bg-primary/10"
                          : "border-border bg-card hover:border-primary/40"
                      }`}
                    >
                      <span className="flex items-center gap-2 text-sm font-semibold text-foreground">
                        <span aria-hidden="true">{template.metadata.icon}</span>
                        {template.metadata.name_ja}
                      </span>
                      <span className="text-xs leading-5 text-muted">
                        {template.metadata.description_ja}
                      </span>
                      <span className="text-xs text-muted">
                        {t("ontologyInterchange.templates.counts", {
                          entities: template.entity_count,
                          relationships: template.relationship_count,
                          terms: template.term_count,
                        })}
                      </span>
                    </button>
                  );
                })}
              </div>
            )}
            {selectedTemplate ? (
              <div className="grid gap-3 rounded-md border border-border bg-card p-3">
                {applyResult && applyResult.unresolved.length > 0 && !applyRegistered ? (
                  <div className="grid gap-2">
                    <p className="text-sm font-medium text-foreground">
                      {t("ontologyInterchange.templates.overridesLabel")}
                    </p>
                    {applyResult.unresolved.map((key) => (
                      <label key={key} className="grid gap-1 text-sm text-foreground">
                        <span>
                          {key}({t("ontologyInterchange.templates.overrideHint")})
                        </span>
                        <input
                          type="text"
                          value={overrides[key] ?? ""}
                          placeholder="OWNER.OBJECT"
                          className={inputClass}
                          onChange={(event) => {
                            // updater 実行時には currentTarget が無効化されるため先に読む
                            const value = event.currentTarget.value;
                            setOverrides((current) => ({ ...current, [key]: value }));
                          }}
                        />
                      </label>
                    ))}
                  </div>
                ) : null}
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    variant="secondary"
                    size="md"
                    loading={applyBusy === "preview"}
                    disabled={applyBusy !== ""}
                    onClick={() => void runTemplate(true)}
                    data-testid="ontology-template-preview"
                  >
                    {t("ontologyInterchange.templates.preview")}
                  </Button>
                  <Button
                    type="button"
                    variant="primary"
                    size="md"
                    loading={applyBusy === "apply"}
                    disabled={applyBusy !== ""}
                    onClick={() => void runTemplate(false)}
                    data-testid="ontology-template-apply"
                  >
                    {t("ontologyInterchange.templates.apply")}
                  </Button>
                </div>
                {applyError ? <Banner severity="danger">{applyError}</Banner> : null}
                {applyResult ? (
                  <InterchangeResult result={applyResult} registered={applyRegistered} />
                ) : null}
              </div>
            ) : null}
          </section>

          {/* --- OWL RDF import / export --- */}
          <section
            className="grid gap-3 border-t border-border pt-4"
            aria-label={t("ontologyInterchange.rdf.title")}
          >
            <div className="flex items-center gap-2">
              <Download size={16} aria-hidden="true" className="text-muted" />
              <h3 className="text-sm font-semibold text-foreground">
                {t("ontologyInterchange.rdf.title")}
              </h3>
            </div>
            <p className="text-sm text-muted">{t("ontologyInterchange.rdf.hint")}</p>
            <div className="grid items-end gap-3 sm:grid-cols-[minmax(0,1fr)_auto_auto]">
              <label className="grid min-w-0 gap-1 text-sm font-medium text-foreground">
                <span>{t("ontologyInterchange.export.revisionLabel")}</span>
                <select
                  value={exportRevisionId}
                  onChange={(event) => setExportRevisionId(event.currentTarget.value)}
                  className={selectClass}
                  data-testid="ontology-export-revision"
                >
                  {revisions.map((revision) => (
                    <option key={revision.id} value={revision.id}>
                      v{revision.version}({revision.status})
                    </option>
                  ))}
                </select>
              </label>
              <label className="grid gap-1 text-sm font-medium text-foreground">
                <span>{t("ontologyInterchange.export.formatLabel")}</span>
                <select
                  value={exportFormat}
                  onChange={(event) =>
                    setExportFormat(event.currentTarget.value as "rdfxml" | "turtle")
                  }
                  className={selectClass}
                >
                  <option value="rdfxml">RDF/XML (OWL)</option>
                  <option value="turtle">Turtle</option>
                </select>
              </label>
              <Button
                type="button"
                variant="secondary"
                size="md"
                loading={exportBusy}
                disabled={!exportRevisionId}
                onClick={() => void runExport()}
                data-testid="ontology-export-download"
              >
                {t("ontologyInterchange.export.action")}
              </Button>
            </div>
            <div className="grid items-end gap-3 sm:grid-cols-[minmax(0,1fr)_auto]">
              <FileInputControl
                label={t("ontologyInterchange.import.fileLabel")}
                accept=".rdf,.owl,.xml,.ttl"
                filename={importFile?.name ?? ""}
                emptyText={t("ontologyInterchange.import.fileEmpty")}
                pickText={t("ontologyInterchange.import.filePick")}
                dataTestId="ontology-import-file"
                onPick={(file) => {
                  setImportFile(file);
                  setImportResult(null);
                  setImportError("");
                }}
                onClear={() => setImportFile(null)}
              />
              <Button
                type="button"
                variant="primary"
                size="md"
                loading={importBusy}
                disabled={!importFile || importBusy}
                onClick={() => void runImport()}
                data-testid="ontology-import-run"
              >
                {t("ontologyInterchange.import.action")}
              </Button>
            </div>
            {importError ? <Banner severity="danger">{importError}</Banner> : null}
            {importResult ? <InterchangeResult result={importResult} registered /> : null}
          </section>
        </div>
      )}
    </DbObjectManagementPanelShell>
  );
}
