import { useEffect, useMemo, useState } from "react";
import {
  BookOpen,
  ChevronLeft,
  ChevronRight,
  Download,
  Layers3,
  RefreshCw,
  Save,
  UserCog,
} from "lucide-react";

import {
  Button,
  Banner,
  EmptyState,
  FormStatus,
  PageHeader,
  StatusBadge,
} from "@engchina/production-ready-ui";

import { apiGet, apiPatch } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import {
  FileInputControl,
  downloadBlob,
} from "../components/DbAdminShared";
import {
  DbObjectManagementPanelShell,
  DbObjectManagementStatusBar,
  DbObjectManagementTabs,
  DbObjectPanelHeader,
  type DbObjectTab,
} from "../components/DbObjectManagementShared";
import type {
  LegacyLearningMaterialData,
  Nl2SqlProfile,
  ProfileLearningMaterialImportData,
  ProfileUpsertPayload,
} from "../types";

type ActiveView = "globalTerms" | "globalRules" | "profile";
type LegacyKind = "terms" | "rules";
type MessageTone = "success" | "danger";

const GLOSSARY_RULES_ID = "glossary-rules";
const GLOBAL_PAGE_SIZE = 10;
const GLOBAL_PREVIEW_TEXT_CLASS =
  "max-h-[15rem] min-w-0 overflow-y-auto whitespace-pre-wrap [overflow-wrap:anywhere] pr-2 leading-6";

interface GlossaryFormState {
  glossaryText: string;
  sqlRulesText: string;
  fewShotText: string;
}

interface StatusMessage {
  text: string;
  tone: MessageTone;
}

const EMPTY_FORM: GlossaryFormState = {
  glossaryText: "",
  sqlRulesText: "",
  fewShotText: "",
};

function glossaryToText(glossary: Record<string, string>) {
  return Object.entries(glossary)
    .map(([term, replacement]) => `${term}=${replacement}`)
    .join("\n");
}

function textToGlossary(text: string): Record<string, string> {
  return Object.fromEntries(
    text
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const [term, ...rest] = line.split("=");
        return [term.trim(), rest.join("=").trim()];
      })
      .filter(([term, replacement]) => term && replacement)
  );
}

function lines(text: string) {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function fewShotToText(examples: Array<Record<string, string>>) {
  return examples.map((example) => `${example.question ?? ""} => ${example.sql ?? ""}`).join("\n");
}

function textToFewShot(text: string) {
  return lines(text)
    .map((line) => {
      const [question, ...rest] = line.split("=>");
      return { question: question.trim(), sql: rest.join("=>").trim() };
    })
    .filter((example) => example.question && example.sql);
}

function escapeCsvCell(value: string) {
  const normalized = value.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  if (/[",\n]/.test(normalized)) {
    return `"${normalized.replace(/"/g, '""')}"`;
  }
  return normalized;
}

function parseDelimited(text: string) {
  const delimiter = text.includes("\t") ? "\t" : ",";
  const rows: string[][] = [];
  let current = "";
  let row: string[] = [];
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    const next = text[index + 1];
    if (char === '"' && quoted && next === '"') {
      current += '"';
      index += 1;
    } else if (char === '"') {
      quoted = !quoted;
    } else if (char === delimiter && !quoted) {
      row.push(current);
      current = "";
    } else if ((char === "\n" || char === "\r") && !quoted) {
      if (char === "\r" && next === "\n") index += 1;
      row.push(current);
      rows.push(row);
      row = [];
      current = "";
    } else {
      current += char;
    }
  }
  row.push(current);
  rows.push(row);
  return rows.filter((items) => items.some((item) => item.trim()));
}

function rowsToObjects(text: string) {
  const rows = parseDelimited(text);
  const [header = [], ...body] = rows;
  const normalized = header.map((item) => item.trim().toUpperCase());
  return body.map((row) =>
    Object.fromEntries(normalized.map((key, index) => [key, row[index]?.trim() ?? ""]))
  );
}

function downloadCsv(filename: string, text: string) {
  const blob = new Blob([text], { type: "text/csv;charset=utf-8" });
  downloadBlob(filename, blob);
}

async function readFileText(file: File) {
  return file.text();
}

function profileToForm(profile: Nl2SqlProfile): GlossaryFormState {
  return {
    glossaryText: glossaryToText(profile.glossary),
    sqlRulesText: profile.sql_rules.join("\n"),
    fewShotText: fewShotToText(profile.few_shot_examples),
  };
}

function profileToPayload(profile: Nl2SqlProfile, form: GlossaryFormState): ProfileUpsertPayload {
  return {
    name: profile.name,
    category: profile.category ?? "",
    description: profile.description,
    allowed_tables: profile.allowed_tables,
    allowed_views: profile.allowed_views ?? [],
    glossary: textToGlossary(form.glossaryText),
    sql_rules: lines(form.sqlRulesText),
    default_row_limit: profile.default_row_limit,
    safety_policy: profile.safety_policy,
    few_shot_examples: textToFewShot(form.fewShotText),
    select_ai_config: profile.select_ai_config,
  };
}

export function GlossaryRulesPage() {
  const [profiles, setProfiles] = useState<Nl2SqlProfile[]>([]);
  const [legacyMaterial, setLegacyMaterial] = useState<LegacyLearningMaterialData>({
    glossary: {},
    rules: [],
  });
  const [selectedId, setSelectedId] = useState("");
  const [form, setForm] = useState<GlossaryFormState>(EMPTY_FORM);
  const [activeView, setActiveView] = useState<ActiveView>("globalTerms");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [legacyBusy, setLegacyBusy] = useState<LegacyKind | "">("");
  const [materialBusy, setMaterialBusy] = useState(false);
  const [materialMode, setMaterialMode] = useState<"merge" | "replace">("merge");
  const [message, setMessage] = useState<StatusMessage | null>(null);
  const [lastLoadedAt, setLastLoadedAt] = useState("");
  const [legacyTermsFilename, setLegacyTermsFilename] = useState("");
  const [legacyRulesFilename, setLegacyRulesFilename] = useState("");
  const [materialFilename, setMaterialFilename] = useState("");

  const selectedProfile = useMemo(
    () => profiles.find((profile) => profile.id === selectedId) ?? null,
    [profiles, selectedId]
  );

  const legacyTerms = useMemo(
    () =>
      Object.entries(legacyMaterial.glossary).map(([term, definition]) => ({
        term,
        definition,
      })),
    [legacyMaterial.glossary]
  );
  const legacyRules = legacyMaterial.rules;

  const load = async (announce = false) => {
    setLoading(true);
    setMessage(null);
    try {
      const [profileData, legacyData] = await Promise.all([
        apiGet<Nl2SqlProfile[]>("/api/nl2sql/profiles"),
        apiGet<LegacyLearningMaterialData>("/api/nl2sql/legacy-learning-material"),
      ]);
      setProfiles(profileData);
      setLegacyMaterial(legacyData);
      const next = profileData.find((profile) => profile.id === selectedId) ?? profileData[0] ?? null;
      setSelectedId(next?.id ?? "");
      setForm(next ? profileToForm(next) : EMPTY_FORM);
      setLastLoadedAt(new Date().toISOString());
      if (announce) {
        setMessage({ tone: "success", text: t("glossary.message.serverLoaded") });
      }
    } catch (err) {
      setMessage({ tone: "danger", text: err instanceof Error ? err.message : t("glossary.error.load") });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const selectProfile = (profileId: string) => {
    const profile = profiles.find((item) => item.id === profileId) ?? null;
    setSelectedId(profileId);
    setForm(profile ? profileToForm(profile) : EMPTY_FORM);
    setMessage(null);
  };

  const save = async () => {
    if (!selectedProfile) return;
    setSaving(true);
    setMessage(null);
    try {
      const updated = await apiPatch<Nl2SqlProfile>(
        `/api/nl2sql/profiles/${selectedProfile.id}`,
        profileToPayload(selectedProfile, form)
      );
      setProfiles((current) => current.map((profile) => (profile.id === updated.id ? updated : profile)));
      setForm(profileToForm(updated));
      setMessage({ tone: "success", text: t("glossary.message.saved") });
    } catch (err) {
      setMessage({ tone: "danger", text: err instanceof Error ? err.message : t("glossary.error.save") });
    } finally {
      setSaving(false);
    }
  };

  const importLegacyMaterial = async (file: File, kind: LegacyKind) => {
    if (kind === "terms") setLegacyTermsFilename(file.name);
    if (kind === "rules") setLegacyRulesFilename(file.name);
    setLegacyBusy(kind);
    setMessage(null);
    try {
      const data = await uploadLegacyLearningMaterialFile(kind, file);
      setLegacyMaterial(data);
      setMessage({
        tone: "success",
        text: t("glossary.message.legacyImported", {
          terms: Object.keys(data.glossary).length,
          rules: data.rules.length,
        }),
      });
    } catch (err) {
      setMessage({ tone: "danger", text: err instanceof Error ? err.message : t("glossary.error.importMaterial") });
    } finally {
      setLegacyBusy("");
    }
  };

  const exportLegacyMaterial = async (kind: LegacyKind) => {
    setLegacyBusy(kind);
    setMessage(null);
    try {
      const filename = kind === "terms" ? "terms.xlsx" : "rules.xlsx";
      const response = await fetch(`/api/nl2sql/legacy-learning-material/${kind}/export.xlsx`, {
        headers: { Accept: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" },
      });
      if (!response.ok) throw new Error(t("glossary.error.exportMaterial"));
      downloadBlob(filename, await response.blob());
    } catch (err) {
      setMessage({ tone: "danger", text: err instanceof Error ? err.message : t("glossary.error.exportMaterial") });
    } finally {
      setLegacyBusy("");
    }
  };

  const exportTerms = () => {
    const rows = Object.entries(textToGlossary(form.glossaryText)).map(
      ([term, definition]) => `${escapeCsvCell(term)},${escapeCsvCell(definition)}`
    );
    downloadCsv("nl2sql_terms.csv", ["TERM,DEFINITION", ...rows].join("\n"));
  };

  const exportRules = () => {
    const rows = lines(form.sqlRulesText).map((rule) => escapeCsvCell(rule));
    downloadCsv("nl2sql_rules.csv", ["RULE", ...rows].join("\n"));
  };

  const exportExamples = () => {
    const rows = textToFewShot(form.fewShotText).map(
      (example) => `${escapeCsvCell(example.question)},${escapeCsvCell(example.sql)}`
    );
    downloadCsv("nl2sql_few_shot_examples.csv", ["QUESTION,SQL", ...rows].join("\n"));
  };

  const importTerms = async (file: File) => {
    try {
      const objects = rowsToObjects(await readFileText(file));
      const next = objects
        .map((item) => {
          const term = item.TERM || item.KEY || item.WORD;
          const definition = item.DEFINITION || item.DESCRIPTION || item.VALUE;
          return term && definition ? `${term}=${definition}` : "";
        })
        .filter(Boolean)
        .join("\n");
      if (!next) throw new Error(t("glossary.error.noImportableRows"));
      setForm((current) => ({ ...current, glossaryText: next }));
      setMessage({ tone: "success", text: t("glossary.message.imported", { count: lines(next).length }) });
    } catch (err) {
      setMessage({ tone: "danger", text: err instanceof Error ? err.message : t("glossary.error.importMaterial") });
    }
  };

  const importRules = async (file: File) => {
    try {
      const objects = rowsToObjects(await readFileText(file));
      const next = objects
        .map((item) => item.RULE || item.TEXT)
        .filter(Boolean)
        .join("\n");
      if (!next) throw new Error(t("glossary.error.noImportableRows"));
      setForm((current) => ({ ...current, sqlRulesText: next }));
      setMessage({ tone: "success", text: t("glossary.message.imported", { count: lines(next).length }) });
    } catch (err) {
      setMessage({ tone: "danger", text: err instanceof Error ? err.message : t("glossary.error.importMaterial") });
    }
  };

  const importExamples = async (file: File) => {
    try {
      const objects = rowsToObjects(await readFileText(file));
      const next = objects
        .map((item) => {
          const question = item.QUESTION || item.TEXT || item.PROMPT;
          const sql = item.SQL || item.EXPECTED_SQL;
          return question && sql ? `${question} => ${sql}` : "";
        })
        .filter(Boolean)
        .join("\n");
      if (!next) throw new Error(t("glossary.error.noImportableRows"));
      setForm((current) => ({ ...current, fewShotText: next }));
      setMessage({ tone: "success", text: t("glossary.message.imported", { count: lines(next).length }) });
    } catch (err) {
      setMessage({ tone: "danger", text: err instanceof Error ? err.message : t("glossary.error.importMaterial") });
    }
  };

  const importLearningMaterial = async (file: File) => {
    if (!selectedProfile) return;
    setMaterialFilename(file.name);
    setMaterialBusy(true);
    setMessage(null);
    try {
      const data = await uploadProfileLearningMaterialFile(selectedProfile.id, file, materialMode);
      setProfiles((current) => current.map((profile) => (profile.id === data.profile.id ? data.profile : profile)));
      setSelectedId(data.profile.id);
      setForm(profileToForm(data.profile));
      setMessage({
        tone: "success",
        text: t("glossary.message.materialImported", {
          terms: data.imported_terms,
          rules: data.imported_rules,
          examples: data.imported_examples,
        }),
      });
    } catch (err) {
      setMessage({ tone: "danger", text: err instanceof Error ? err.message : t("glossary.error.importMaterial") });
    } finally {
      setMaterialBusy(false);
    }
  };

  const exportLearningMaterial = async () => {
    if (!selectedProfile) return;
    setMaterialBusy(true);
    setMessage(null);
    try {
      const response = await fetch(
        `/api/nl2sql/profiles/${encodeURIComponent(selectedProfile.id)}/learning-material/export.xlsx`,
        { headers: { Accept: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" } }
      );
      if (!response.ok) {
        throw new Error(t("glossary.error.exportMaterial"));
      }
      downloadBlob(`nl2sql_${selectedProfile.id}_learning_material.xlsx`, await response.blob());
    } catch (err) {
      setMessage({ tone: "danger", text: err instanceof Error ? err.message : t("glossary.error.exportMaterial") });
    } finally {
      setMaterialBusy(false);
    }
  };

  return (
    <>
      <PageHeader
        title={t("nav.glossaryRules")}
        subtitle={t("glossary.subtitle")}
      />
      <main className="grid gap-4 p-4 lg:p-8">
        <GlossaryStatusBar
          termsCount={legacyTerms.length}
          rulesCount={legacyRules.length}
          profileCount={profiles.length}
          selectedProfileName={selectedProfile?.name ?? ""}
          lastLoadedAt={lastLoadedAt}
          loading={loading}
          onRefresh={() => void load(true)}
        />

        {message?.tone === "danger" ? (
          <Banner severity="danger">{message.text}</Banner>
        ) : (
          <FormStatus tone="success" message={message?.text} />
        )}

        <DbObjectManagementTabs
          activeView={activeView}
          tabs={[
            { id: "globalTerms", label: t("glossary.tabs.globalTerms"), icon: BookOpen },
            { id: "globalRules", label: t("glossary.tabs.globalRules"), icon: Layers3 },
            { id: "profile", label: t("glossary.tabs.profile"), icon: UserCog },
          ] satisfies Array<DbObjectTab<ActiveView>>}
          idPrefix={GLOSSARY_RULES_ID}
          ariaLabel={t("glossary.tabs.label")}
          onViewChange={setActiveView}
        />

        {activeView === "globalTerms" ? (
          <DbObjectManagementPanelShell
            id="glossary-rules-panel-globalTerms"
            labelledBy="glossary-rules-tab-globalTerms"
            idPrefix={GLOSSARY_RULES_ID}
            ariaLabel={t("glossary.globalTerms.workspace")}
          >
            <GlobalMaterialPanel
              kind="terms"
              title={t("glossary.globalTerms.title")}
              description={t("glossary.globalTerms.hint")}
              countLabel={t("glossary.count.terms", { count: legacyTerms.length })}
              importLabel={t("glossary.globalTerms.import")}
              exportLabel={t("glossary.globalTerms.export")}
              filename={legacyTermsFilename}
              busy={legacyBusy === "terms"}
              rows={legacyTerms}
              onImport={(file) => void importLegacyMaterial(file, "terms")}
              onExport={() => void exportLegacyMaterial("terms")}
            />
          </DbObjectManagementPanelShell>
        ) : activeView === "globalRules" ? (
          <DbObjectManagementPanelShell
            id="glossary-rules-panel-globalRules"
            labelledBy="glossary-rules-tab-globalRules"
            idPrefix={GLOSSARY_RULES_ID}
            ariaLabel={t("glossary.globalRules.workspace")}
          >
            <GlobalMaterialPanel
              kind="rules"
              title={t("glossary.globalRules.title")}
              description={t("glossary.globalRules.hint")}
              countLabel={t("glossary.count.rules", { count: legacyRules.length })}
              importLabel={t("glossary.globalRules.import")}
              exportLabel={t("glossary.globalRules.export")}
              filename={legacyRulesFilename}
              busy={legacyBusy === "rules"}
              rows={legacyRules}
              onImport={(file) => void importLegacyMaterial(file, "rules")}
              onExport={() => void exportLegacyMaterial("rules")}
            />
          </DbObjectManagementPanelShell>
        ) : (
          <DbObjectManagementPanelShell
            id="glossary-rules-panel-profile"
            labelledBy="glossary-rules-tab-profile"
            idPrefix={GLOSSARY_RULES_ID}
            ariaLabel={t("glossary.profile.workspace")}
            splitId="glossary-rules-profile"
            preferredWidePane="right"
          >
            <ProfileListPanel
              profiles={profiles}
              selectedId={selectedId}
              onSelect={selectProfile}
            />
            <ProfileLearningMaterialPanel
              profile={selectedProfile}
              form={form}
              materialMode={materialMode}
              materialFilename={materialFilename}
              materialBusy={materialBusy}
              saving={saving}
              onFormChange={setForm}
              onModeChange={setMaterialMode}
              onImportLearningMaterial={(file) => void importLearningMaterial(file)}
              onExportLearningMaterial={() => void exportLearningMaterial()}
              onSave={() => void save()}
              onExportTerms={exportTerms}
              onExportRules={exportRules}
              onExportExamples={exportExamples}
              onImportTerms={(file) => void importTerms(file)}
              onImportRules={(file) => void importRules(file)}
              onImportExamples={(file) => void importExamples(file)}
            />
          </DbObjectManagementPanelShell>
        )}
      </main>
    </>
  );
}

async function uploadProfileLearningMaterialFile(
  profileId: string,
  file: File,
  mode: "merge" | "replace"
): Promise<ProfileLearningMaterialImportData> {
  const form = new FormData();
  form.append("file", file);
  form.append("mode", mode);
  const response = await fetch(
    `/api/nl2sql/profiles/${encodeURIComponent(profileId)}/learning-material/import`,
    {
      method: "POST",
      body: form,
      headers: { Accept: "application/json" },
    }
  );
  const payload = (await response.json()) as {
    data?: ProfileLearningMaterialImportData;
    error?: string;
    detail?: string;
  };
  if (!response.ok || !payload.data) {
    throw new Error(payload.error || payload.detail || t("glossary.error.importMaterial"));
  }
  return payload.data;
}

async function uploadLegacyLearningMaterialFile(
  kind: LegacyKind,
  file: File
): Promise<LegacyLearningMaterialData> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(`/api/nl2sql/legacy-learning-material/${kind}/import`, {
    method: "POST",
    body: form,
    headers: { Accept: "application/json" },
  });
  const payload = (await response.json()) as {
    data?: LegacyLearningMaterialData;
    error?: string;
    detail?: string;
  };
  if (!response.ok || !payload.data) {
    throw new Error(payload.error || payload.detail || t("glossary.error.importMaterial"));
  }
  return payload.data;
}

function GlossaryStatusBar({
  termsCount,
  rulesCount,
  profileCount,
  selectedProfileName,
  lastLoadedAt,
  loading,
  onRefresh,
}: {
  termsCount: number;
  rulesCount: number;
  profileCount: number;
  selectedProfileName: string;
  lastLoadedAt: string;
  loading: boolean;
  onRefresh: () => void;
}) {
  return (
    <DbObjectManagementStatusBar
      ariaLabel={t("glossary.status.label")}
      metricColumnsClass="sm:grid-cols-2 xl:grid-cols-5"
      metrics={[
        { label: t("glossary.status.terms"), value: formatNumber(termsCount), emphasis: true },
        { label: t("glossary.status.rules"), value: formatNumber(rulesCount), emphasis: true },
        { label: t("glossary.status.profiles"), value: formatNumber(profileCount), emphasis: true },
        {
          label: t("glossary.status.selectedProfile"),
          value: selectedProfileName || t("glossary.status.selectedProfileEmpty"),
        },
        { label: t("glossary.status.lastLoaded"), value: formatDateTime(lastLoadedAt) },
      ]}
      actions={
        <Button type="button" variant="secondary" size="sm" loading={loading} onClick={onRefresh}>
          <RefreshCw size={15} aria-hidden="true" />
          <span>{t("glossary.action.refresh")}</span>
        </Button>
      }
    />
  );
}

function GlobalMaterialPanel({
  kind,
  title,
  description,
  countLabel,
  importLabel,
  exportLabel,
  filename,
  busy,
  rows,
  onImport,
  onExport,
}: {
  kind: LegacyKind;
  title: string;
  description: string;
  countLabel: string;
  importLabel: string;
  exportLabel: string;
  filename: string;
  busy: boolean;
  rows: Array<{ term: string; definition: string }> | string[];
  onImport: (file: File) => void;
  onExport: () => void;
}) {
  return (
    <section className="grid min-w-0 content-start gap-3 rounded-md border border-slate-200 bg-slate-50 p-3">
      <DbObjectPanelHeader
        title={title}
        description={description}
        icon={kind === "terms" ? BookOpen : Layers3}
        action={<StatusBadge variant="neutral" label={countLabel} />}
      />
      <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto] md:items-end">
        <FileInputControl
          label={importLabel}
          ariaLabel={importLabel}
          accept=".xlsx,.xlsm,.csv,.tsv,.txt"
          filename={filename}
          selectedText={filename}
          emptyText={t("glossary.file.emptyWorkbook")}
          pickText={t("glossary.file.pickWorkbook")}
          replaceText={t("glossary.file.replaceWorkbook")}
          icon="spreadsheet"
          disabled={busy}
          onPick={onImport}
        />
        <Button
          type="button"
          variant="secondary"
          size="sm"
          className="min-h-11 md:self-end"
          loading={busy}
          onClick={onExport}
        >
          <Download size={15} aria-hidden="true" />
          <span>{exportLabel}</span>
        </Button>
      </div>
      <GlobalPreviewTable kind={kind} rows={rows} />
    </section>
  );
}

function GlobalPreviewTable({
  kind,
  rows,
}: {
  kind: LegacyKind;
  rows: Array<{ term: string; definition: string }> | string[];
}) {
  const [page, setPage] = useState(1);
  const totalPages = Math.max(1, Math.ceil(rows.length / GLOBAL_PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const start = (currentPage - 1) * GLOBAL_PAGE_SIZE;
  const visibleRows = rows.slice(start, start + GLOBAL_PAGE_SIZE);

  useEffect(() => {
    setPage(1);
  }, [rows]);

  if (rows.length === 0) {
    return (
      <div className="rounded-md border border-slate-200 bg-white p-4">
        <EmptyState title={t("glossary.legacy.empty")} hint={t("glossary.legacy.emptyHint")} />
      </div>
    );
  }

  const isTerms = kind === "terms";
  return (
    <div className="grid gap-2" data-testid={`glossary-${kind}-preview`}>
      <div className="overflow-hidden rounded-md border border-slate-200 bg-white">
        <div className="overflow-x-auto">
          <table className="w-full table-fixed divide-y divide-slate-200 text-left text-sm">
            <colgroup>
              <col className="w-12" />
              {isTerms && <col className="w-32 sm:w-56" />}
              <col />
            </colgroup>
            <thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-600">
              <tr>
                <th scope="col" className="px-3 py-2 text-right">
                  {t("glossary.preview.rowNumber")}
                </th>
                {isTerms && (
                  <th scope="col" className="px-3 py-2">
                    TERM
                  </th>
                )}
                <th scope="col" className="px-3 py-2">
                  {isTerms ? "DEFINITION" : "RULE"}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 text-slate-800">
              {visibleRows.map((row, index) => {
                const absoluteIndex = start + index;
                if (isTerms) {
                  const termRow = row as { term: string; definition: string };
                  return (
                    <tr key={`${termRow.term}-${absoluteIndex}`}>
                      <td
                        className="px-3 py-2 text-right text-xs tabular-nums text-slate-500"
                        data-testid="glossary-terms-row-number"
                      >
                        {absoluteIndex + 1}
                      </td>
                      <td className="px-3 py-2 align-top font-mono text-xs text-slate-700 [overflow-wrap:anywhere]">
                        {termRow.term}
                      </td>
                      <td className="min-w-0 px-3 py-2 align-top">
                        <div className={GLOBAL_PREVIEW_TEXT_CLASS} data-testid="glossary-definition-preview-text">
                          {termRow.definition}
                        </div>
                      </td>
                    </tr>
                  );
                }
                const rule = row as string;
                return (
                  <tr key={`${rule}-${absoluteIndex}`}>
                    <td
                      className="px-3 py-2 text-right text-xs tabular-nums text-slate-500"
                      data-testid="glossary-rules-row-number"
                    >
                      {absoluteIndex + 1}
                    </td>
                    <td className="min-w-0 px-3 py-2 align-top">
                      <div className={GLOBAL_PREVIEW_TEXT_CLASS} data-testid="glossary-rule-preview-text">
                        {rule}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
      {rows.length > GLOBAL_PAGE_SIZE && (
        <nav
          className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-600"
          aria-label={t("glossary.pagination.label")}
          data-testid={`glossary-${kind}-pagination`}
        >
          <span>
            {t("glossary.pagination.range", {
              start: start + 1,
              end: Math.min(start + GLOBAL_PAGE_SIZE, rows.length),
              total: rows.length,
            })}
          </span>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={currentPage <= 1}
              onClick={() => setPage((value) => Math.max(1, value - 1))}
            >
              <ChevronLeft size={15} aria-hidden="true" />
              <span>{t("glossary.pagination.prev")}</span>
            </Button>
            <span className="inline-flex min-h-8 items-center rounded-md border border-slate-200 px-3 text-slate-700">
              {t("glossary.pagination.page", { page: currentPage, total: totalPages })}
            </span>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={currentPage >= totalPages}
              onClick={() => setPage((value) => Math.min(totalPages, value + 1))}
            >
              <span>{t("glossary.pagination.next")}</span>
              <ChevronRight size={15} aria-hidden="true" />
            </Button>
          </div>
        </nav>
      )}
    </div>
  );
}

function ProfileListPanel({
  profiles,
  selectedId,
  onSelect,
}: {
  profiles: Nl2SqlProfile[];
  selectedId: string;
  onSelect: (profileId: string) => void;
}) {
  return (
    <section className="grid min-w-0 content-start gap-3" aria-labelledby="glossary-profile-list-heading">
      <DbObjectPanelHeader
        headingId="glossary-profile-list-heading"
        icon={BookOpen}
        title={t("glossary.profiles.title")}
        description={t("glossary.profiles.hint")}
        action={<StatusBadge variant="info" label={t("glossary.status.profilesCount", { count: profiles.length })} />}
      />
      {profiles.length === 0 ? (
        <EmptyState title={t("profiles.empty.title")} hint={t("profiles.empty.hint")} />
      ) : (
        <div className="grid gap-2">
          {profiles.map((profile) => {
            const selected = selectedId === profile.id;
            return (
              <button
                key={profile.id}
                type="button"
                onClick={() => onSelect(profile.id)}
                aria-pressed={selected}
                className={`grid min-h-24 gap-2 rounded-md border p-3 text-left transition-colors focus:outline-none focus:ring-2 focus:ring-sky-200 ${
                  selected
                    ? "border-sky-300 bg-sky-50 text-sky-950"
                    : "border-slate-200 bg-white text-slate-900 hover:bg-slate-50"
                }`}
              >
                <span className="font-semibold">{profile.name}</span>
                <span className="flex flex-wrap gap-2">
                  <StatusBadge
                    variant="neutral"
                    label={t("glossary.count.terms", { count: Object.keys(profile.glossary).length })}
                  />
                  <StatusBadge
                    variant="neutral"
                    label={t("glossary.count.rules", { count: profile.sql_rules.length })}
                  />
                  <StatusBadge
                    variant="neutral"
                    label={t("glossary.count.examples", { count: profile.few_shot_examples.length })}
                  />
                </span>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}

function ProfileLearningMaterialPanel({
  profile,
  form,
  materialMode,
  materialFilename,
  materialBusy,
  saving,
  onFormChange,
  onModeChange,
  onImportLearningMaterial,
  onExportLearningMaterial,
  onSave,
  onExportTerms,
  onExportRules,
  onExportExamples,
  onImportTerms,
  onImportRules,
  onImportExamples,
}: {
  profile: Nl2SqlProfile | null;
  form: GlossaryFormState;
  materialMode: "merge" | "replace";
  materialFilename: string;
  materialBusy: boolean;
  saving: boolean;
  onFormChange: (updater: (current: GlossaryFormState) => GlossaryFormState) => void;
  onModeChange: (mode: "merge" | "replace") => void;
  onImportLearningMaterial: (file: File) => void;
  onExportLearningMaterial: () => void;
  onSave: () => void;
  onExportTerms: () => void;
  onExportRules: () => void;
  onExportExamples: () => void;
  onImportTerms: (file: File) => void;
  onImportRules: (file: File) => void;
  onImportExamples: (file: File) => void;
}) {
  return (
    <section className="grid min-w-0 content-start gap-4" aria-labelledby="glossary-profile-editor-heading">
      <DbObjectPanelHeader
        headingId="glossary-profile-editor-heading"
        icon={UserCog}
        title={
          profile
            ? t("glossary.profileOverride.titleWithName", { name: profile.name })
            : t("glossary.profileOverride.title")
        }
        description={t("glossary.profileOverride.hint")}
        action={
          <Button
            type="button"
            size="sm"
            loading={saving}
            disabled={!profile}
            onClick={onSave}
          >
            <Save size={15} aria-hidden="true" />
            <span>{t("glossary.action.save")}</span>
          </Button>
        }
      />

      <section className="grid gap-3 rounded-md border border-slate-200 bg-slate-50 p-3">
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,0.85fr)_auto] lg:items-end">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-slate-900">{t("glossary.material.title")}</p>
            <p className="mt-1 text-sm leading-6 text-slate-600">{t("glossary.material.hint")}</p>
          </div>
          <FileInputControl
            label={t("glossary.action.importWorkbook")}
            ariaLabel={t("glossary.action.importWorkbook")}
            accept=".csv,.tsv,.txt,.xlsx,.xlsm"
            filename={materialFilename}
            selectedText={materialFilename}
            emptyText={t("glossary.file.emptyWorkbook")}
            pickText={t("glossary.file.pickWorkbook")}
            replaceText={t("glossary.file.replaceWorkbook")}
            icon="spreadsheet"
            disabled={!profile || materialBusy}
            onPick={onImportLearningMaterial}
          />
          <Button
            type="button"
            variant="secondary"
            size="sm"
            className="min-h-11 lg:self-end"
            loading={materialBusy}
            disabled={!profile}
            onClick={onExportLearningMaterial}
          >
            <Download size={15} aria-hidden="true" />
            <span>{t("glossary.action.exportWorkbook")}</span>
          </Button>
        </div>
        <label className="flex min-h-11 items-center gap-3 rounded-md border border-slate-200 bg-white px-3 text-sm font-medium text-slate-800">
          <input
            type="checkbox"
            checked={materialMode === "replace"}
            onChange={(event) => onModeChange(event.currentTarget.checked ? "replace" : "merge")}
            className="h-4 w-4 rounded border-slate-300 text-sky-700 focus:ring-sky-500"
          />
          <span>{t("glossary.material.replace")}</span>
        </label>
      </section>

      <div className="grid gap-4 xl:grid-cols-3">
        <LearningMaterialField
          label={t("glossary.field.terms")}
          value={form.glossaryText}
          placeholder={t("profiles.placeholder.glossary")}
          monospace
          onChange={(value) => onFormChange((current) => ({ ...current, glossaryText: value }))}
          onExport={onExportTerms}
          onImport={onImportTerms}
        />
        <LearningMaterialField
          label={t("glossary.field.rules")}
          value={form.sqlRulesText}
          placeholder={t("glossary.field.rulesPlaceholder")}
          onChange={(value) => onFormChange((current) => ({ ...current, sqlRulesText: value }))}
          onExport={onExportRules}
          onImport={onImportRules}
        />
        <LearningMaterialField
          label={t("glossary.field.examples")}
          value={form.fewShotText}
          placeholder={t("profiles.placeholder.fewShot")}
          monospace
          onChange={(value) => onFormChange((current) => ({ ...current, fewShotText: value }))}
          onExport={onExportExamples}
          onImport={onImportExamples}
        />
      </div>
    </section>
  );
}

function LearningMaterialField({
  label,
  value,
  placeholder,
  monospace = false,
  onChange,
  onExport,
  onImport,
}: {
  label: string;
  value: string;
  placeholder: string;
  monospace?: boolean;
  onChange: (value: string) => void;
  onExport: () => void;
  onImport: (file: File) => void;
}) {
  const importLabel = `${label} ${t("glossary.action.importCsv")}`;
  return (
    <section className="grid min-w-0 gap-2 text-sm font-medium text-slate-800">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <span>{label}</span>
        <Button type="button" variant="secondary" size="sm" onClick={onExport}>
          <Download size={15} aria-hidden="true" />
          <span>{t("glossary.action.exportCsv")}</span>
        </Button>
      </div>
      <FileInputControl
        label={importLabel}
        ariaLabel={importLabel}
        accept=".csv,.tsv,.txt"
        filename=""
        emptyText={t("glossary.file.emptyCsv")}
        pickText={t("glossary.action.importCsv")}
        replaceText={t("glossary.file.replaceCsv")}
        icon="file"
        onPick={onImport}
      />
      <textarea
        aria-label={label}
        value={value}
        onChange={(event) => onChange(event.currentTarget.value)}
        rows={14}
        className={`min-h-80 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm leading-6 outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200 ${
          monospace ? "font-mono" : ""
        }`}
        placeholder={placeholder}
      />
    </section>
  );
}
