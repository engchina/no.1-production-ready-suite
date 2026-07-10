import { useEffect, useMemo, useState } from "react";
import { BookOpen, Download, RefreshCw, Save, Upload } from "lucide-react";

import { Button, Card, CardContent, CardHeader, CardTitle, EmptyState, PageHeader, StatusBadge } from "@engchina/production-ready-ui";

import { apiGet, apiPatch } from "@/lib/api";
import { t } from "@/lib/i18n";
import type {
  LegacyLearningMaterialData,
  Nl2SqlProfile,
  ProfileLearningMaterialImportData,
  ProfileUpsertPayload,
} from "../types";

interface GlossaryFormState {
  glossaryText: string;
  sqlRulesText: string;
  fewShotText: string;
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

function downloadText(filename: string, text: string) {
  const blob = new Blob([text], { type: "text/csv;charset=utf-8" });
  downloadBlob(filename, blob);
}

function downloadBlob(filename: string, blob: Blob) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
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
    glossary: textToGlossary(form.glossaryText),
    sql_rules: lines(form.sqlRulesText),
    default_row_limit: profile.default_row_limit,
    safety_policy: profile.safety_policy,
    few_shot_examples: textToFewShot(form.fewShotText),
  };
}

export function GlossaryRulesPage() {
  const [profiles, setProfiles] = useState<Nl2SqlProfile[]>([]);
  const [legacyMaterial, setLegacyMaterial] = useState<LegacyLearningMaterialData>({
    glossary: {},
    rule_entries: [],
  });
  const [selectedId, setSelectedId] = useState("");
  const [form, setForm] = useState<GlossaryFormState>(EMPTY_FORM);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [legacyBusy, setLegacyBusy] = useState(false);
  const [materialBusy, setMaterialBusy] = useState(false);
  const [materialMode, setMaterialMode] = useState<"merge" | "replace">("merge");
  const [message, setMessage] = useState("");

  const selectedProfile = useMemo(
    () => profiles.find((profile) => profile.id === selectedId) ?? null,
    [profiles, selectedId]
  );

  const load = async () => {
    setLoading(true);
    setMessage("");
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
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("glossary.error.load"));
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
    setMessage("");
  };

  const save = async () => {
    if (!selectedProfile) return;
    setSaving(true);
    setMessage("");
    try {
      const updated = await apiPatch<Nl2SqlProfile>(
        `/api/nl2sql/profiles/${selectedProfile.id}`,
        profileToPayload(selectedProfile, form)
      );
      setProfiles((current) => current.map((profile) => (profile.id === updated.id ? updated : profile)));
      setForm(profileToForm(updated));
      setMessage(t("glossary.message.saved"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("glossary.error.save"));
    } finally {
      setSaving(false);
    }
  };

  const importLegacyMaterial = async (
    file: File | undefined,
    kind: "terms" | "rules"
  ) => {
    if (!file) return;
    setLegacyBusy(true);
    setMessage("");
    try {
      const data = await uploadLegacyLearningMaterialFile(kind, file);
      setLegacyMaterial(data);
      setMessage(
        t("glossary.message.legacyImported", {
          terms: Object.keys(data.glossary).length,
          rules: data.rule_entries.length,
        })
      );
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("glossary.error.importMaterial"));
    } finally {
      setLegacyBusy(false);
    }
  };

  const exportLegacyMaterial = async (kind: "terms" | "rules") => {
    setLegacyBusy(true);
    setMessage("");
    try {
      const filename = kind === "terms" ? "terms.xlsx" : "rules.xlsx";
      const response = await fetch(`/api/nl2sql/legacy-learning-material/${kind}/export.xlsx`, {
        headers: { Accept: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" },
      });
      if (!response.ok) throw new Error(t("glossary.error.exportMaterial"));
      downloadBlob(filename, await response.blob());
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("glossary.error.exportMaterial"));
    } finally {
      setLegacyBusy(false);
    }
  };

  const exportTerms = () => {
    const rows = Object.entries(textToGlossary(form.glossaryText)).map(
      ([term, definition]) => `${escapeCsvCell(term)},${escapeCsvCell(definition)}`
    );
    downloadText("nl2sql_terms.csv", ["TERM,DEFINITION", ...rows].join("\n"));
  };

  const exportRules = () => {
    const category = selectedProfile?.name ?? "共通";
    const rows = lines(form.sqlRulesText).map(
      (rule) => `${escapeCsvCell(category)},${escapeCsvCell(rule)}`
    );
    downloadText("nl2sql_rules.csv", ["CATEGORY,RULE", ...rows].join("\n"));
  };

  const exportExamples = () => {
    const rows = textToFewShot(form.fewShotText).map(
      (example) => `${escapeCsvCell(example.question)},${escapeCsvCell(example.sql)}`
    );
    downloadText("nl2sql_few_shot_examples.csv", ["QUESTION,SQL", ...rows].join("\n"));
  };

  const importTerms = async (file: File | undefined) => {
    if (!file) return;
    const objects = rowsToObjects(await readFileText(file));
    const next = objects
      .map((item) => {
        const term = item.TERM || item.KEY || item.WORD;
        const definition = item.DEFINITION || item.DESCRIPTION || item.VALUE;
        return term && definition ? `${term}=${definition}` : "";
      })
      .filter(Boolean)
      .join("\n");
    if (next) {
      setForm((current) => ({ ...current, glossaryText: next }));
      setMessage(t("glossary.message.imported", { count: lines(next).length }));
    }
  };

  const importRules = async (file: File | undefined) => {
    if (!file) return;
    const objects = rowsToObjects(await readFileText(file));
    const next = objects
      .map((item) => item.RULE || item.TEXT)
      .filter(Boolean)
      .join("\n");
    if (next) {
      setForm((current) => ({ ...current, sqlRulesText: next }));
      setMessage(t("glossary.message.imported", { count: lines(next).length }));
    }
  };

  const importExamples = async (file: File | undefined) => {
    if (!file) return;
    const objects = rowsToObjects(await readFileText(file));
    const next = objects
      .map((item) => {
        const question = item.QUESTION || item.TEXT || item.PROMPT;
        const sql = item.SQL || item.EXPECTED_SQL;
        return question && sql ? `${question} => ${sql}` : "";
      })
      .filter(Boolean)
      .join("\n");
    if (next) {
      setForm((current) => ({ ...current, fewShotText: next }));
      setMessage(t("glossary.message.imported", { count: lines(next).length }));
    }
  };

  const importLearningMaterial = async (file: File | undefined) => {
    if (!file || !selectedProfile) return;
    setMaterialBusy(true);
    setMessage("");
    try {
      const data = await uploadProfileLearningMaterialFile(selectedProfile.id, file, materialMode);
      setProfiles((current) => current.map((profile) => (profile.id === data.profile.id ? data.profile : profile)));
      setSelectedId(data.profile.id);
      setForm(profileToForm(data.profile));
      setMessage(
        t("glossary.message.materialImported", {
          terms: data.imported_terms,
          rules: data.imported_rules,
          examples: data.imported_examples,
        })
      );
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("glossary.error.importMaterial"));
    } finally {
      setMaterialBusy(false);
    }
  };

  const exportLearningMaterial = async () => {
    if (!selectedProfile) return;
    setMaterialBusy(true);
    setMessage("");
    try {
      const response = await fetch(
        `/api/nl2sql/profiles/${encodeURIComponent(selectedProfile.id)}/learning-material/export.xlsx`,
        { headers: { Accept: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" } }
      );
      if (!response.ok) {
        throw new Error(t("glossary.error.exportMaterial"));
      }
      const blob = await response.blob();
      downloadBlob(`nl2sql_${selectedProfile.id}_learning_material.xlsx`, blob);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("glossary.error.exportMaterial"));
    } finally {
      setMaterialBusy(false);
    }
  };

  return (
    <>
      <PageHeader
        title={t("nav.glossaryRules")}
        subtitle={t("glossary.subtitle")}
        actions={
          <Button type="button" variant="secondary" size="sm" loading={loading} onClick={() => void load()}>
            <RefreshCw size={15} aria-hidden="true" />
            <span>{t("glossary.action.refresh")}</span>
          </Button>
        }
      />
      <main className="grid gap-5 p-4 lg:p-8">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <BookOpen size={18} aria-hidden="true" />
              {t("glossary.legacy.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4">
            <p className="text-sm leading-6 text-slate-600">{t("glossary.legacy.hint")}</p>
            <div className="grid gap-4 lg:grid-cols-2">
              <LegacyMaterialPanel
                title={t("glossary.legacy.terms")}
                countLabel={t("glossary.count.terms", { count: Object.keys(legacyMaterial.glossary).length })}
                importLabel={t("glossary.legacy.importTerms")}
                exportLabel={t("glossary.legacy.exportTerms")}
                busy={legacyBusy}
                rows={Object.entries(legacyMaterial.glossary).map(([term, definition]) => `${term} = ${definition}`)}
                onImport={(file) => void importLegacyMaterial(file, "terms")}
                onExport={() => void exportLegacyMaterial("terms")}
              />
              <LegacyMaterialPanel
                title={t("glossary.legacy.rules")}
                countLabel={t("glossary.count.rules", { count: legacyMaterial.rule_entries.length })}
                importLabel={t("glossary.legacy.importRules")}
                exportLabel={t("glossary.legacy.exportRules")}
                busy={legacyBusy}
                rows={legacyMaterial.rule_entries.map((entry) => `${entry.category}: ${entry.rule}`)}
                onImport={(file) => void importLegacyMaterial(file, "rules")}
                onExport={() => void exportLegacyMaterial("rules")}
              />
            </div>
          </CardContent>
        </Card>

        <div className="grid gap-5 lg:grid-cols-[minmax(16rem,0.55fr)_minmax(0,1.45fr)]">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <BookOpen size={18} aria-hidden="true" />
                {t("glossary.profiles.title")}
              </CardTitle>
            </CardHeader>
            <CardContent className="grid gap-2">
              {profiles.length === 0 ? (
                <EmptyState title={t("profiles.empty.title")} hint={t("profiles.empty.hint")} />
              ) : (
                profiles.map((profile) => (
                  <button
                    key={profile.id}
                    type="button"
                    onClick={() => selectProfile(profile.id)}
                    className={`grid gap-2 rounded-md border p-3 text-left transition ${
                      selectedId === profile.id ? "border-sky-300 bg-sky-50" : "border-slate-200 bg-white hover:bg-slate-50"
                    }`}
                  >
                    <span className="font-medium text-slate-900">{profile.name}</span>
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
                ))
              )}
            </CardContent>
          </Card>

          <Card>
          <CardHeader>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <CardTitle>
                {selectedProfile
                  ? `${t("glossary.profileOverride.title")}: ${selectedProfile.name}`
                  : t("glossary.profileOverride.title")}
              </CardTitle>
              <Button
                type="button"
                size="sm"
                loading={saving}
                disabled={!selectedProfile}
                onClick={() => void save()}
              >
                <Save size={15} aria-hidden="true" />
                <span>{t("glossary.action.save")}</span>
              </Button>
            </div>
          </CardHeader>
          <CardContent className="grid gap-4">
            {message && (
              <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700" role="status">
                {message}
              </div>
            )}
            <section className="grid gap-3 rounded-md border border-slate-200 bg-slate-50 p-3 md:grid-cols-[minmax(0,1fr)_auto]">
              <div>
                <p className="text-sm font-semibold text-slate-900">{t("glossary.material.title")}</p>
                <p className="mt-1 text-sm leading-6 text-slate-600">{t("glossary.material.hint")}</p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <label className="flex min-h-9 items-center gap-2 rounded-md border border-slate-300 bg-white px-3 text-sm font-medium text-slate-800">
                  <input
                    type="checkbox"
                    checked={materialMode === "replace"}
                    onChange={(event) => setMaterialMode(event.currentTarget.checked ? "replace" : "merge")}
                    className="h-4 w-4 rounded border-slate-300 text-sky-700 focus:ring-sky-500"
                  />
                  <span>{t("glossary.material.replace")}</span>
                </label>
                <label className="relative inline-flex min-h-9 cursor-pointer items-center justify-center gap-1.5 rounded-md border border-slate-300 bg-white px-3 text-sm font-medium text-slate-800 hover:bg-slate-100 focus-within:ring-2 focus-within:ring-sky-200">
                  <Upload size={15} aria-hidden="true" />
                  <span>{t("glossary.action.importWorkbook")}</span>
                  <input
                    type="file"
                    accept=".csv,.tsv,.txt,.xlsx,.xlsm"
                    className="absolute inset-0 cursor-pointer opacity-0"
                    aria-label={t("glossary.action.importWorkbook")}
                    disabled={!selectedProfile || materialBusy}
                    onChange={(event) => {
                      void importLearningMaterial(event.currentTarget.files?.[0]);
                      event.currentTarget.value = "";
                    }}
                  />
                </label>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  loading={materialBusy}
                  disabled={!selectedProfile}
                  onClick={() => void exportLearningMaterial()}
                >
                  <Download size={15} aria-hidden="true" />
                  <span>{t("glossary.action.exportWorkbook")}</span>
                </Button>
              </div>
            </section>
            <div className="grid gap-4 xl:grid-cols-3">
              <section className="grid gap-1 text-sm font-medium text-slate-800">
                <FieldToolbar
                  label={t("glossary.field.terms")}
                  onExport={exportTerms}
                  onImport={(file) => void importTerms(file)}
                />
                <textarea
                  aria-label={t("glossary.field.terms")}
                  value={form.glossaryText}
                  onChange={(event) => setForm((current) => ({ ...current, glossaryText: event.currentTarget.value }))}
                  rows={14}
                  className="min-h-80 rounded-md border border-slate-300 bg-white px-3 py-2 font-mono text-sm leading-6 outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                  placeholder={t("profiles.placeholder.glossary")}
                />
              </section>
              <section className="grid gap-1 text-sm font-medium text-slate-800">
                <FieldToolbar
                  label={t("glossary.field.rules")}
                  onExport={exportRules}
                  onImport={(file) => void importRules(file)}
                />
                <textarea
                  aria-label={t("glossary.field.rules")}
                  value={form.sqlRulesText}
                  onChange={(event) => setForm((current) => ({ ...current, sqlRulesText: event.currentTarget.value }))}
                  rows={14}
                  className="min-h-80 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm leading-6 outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                />
              </section>
              <section className="grid gap-1 text-sm font-medium text-slate-800">
                <FieldToolbar
                  label={t("glossary.field.examples")}
                  onExport={exportExamples}
                  onImport={(file) => void importExamples(file)}
                />
                <textarea
                  aria-label={t("glossary.field.examples")}
                  value={form.fewShotText}
                  onChange={(event) => setForm((current) => ({ ...current, fewShotText: event.currentTarget.value }))}
                  rows={14}
                  className="min-h-80 rounded-md border border-slate-300 bg-white px-3 py-2 font-mono text-sm leading-6 outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                  placeholder={t("profiles.placeholder.fewShot")}
                />
              </section>
            </div>
          </CardContent>
          </Card>
        </div>
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
  kind: "terms" | "rules",
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

function LegacyMaterialPanel({
  title,
  countLabel,
  importLabel,
  exportLabel,
  busy,
  rows,
  onImport,
  onExport,
}: {
  title: string;
  countLabel: string;
  importLabel: string;
  exportLabel: string;
  busy: boolean;
  rows: string[];
  onImport: (file: File | undefined) => void;
  onExport: () => void;
}) {
  return (
    <section className="grid gap-3 rounded-md border border-slate-200 bg-slate-50 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold text-slate-900">{title}</span>
          <StatusBadge variant="neutral" label={countLabel} />
        </div>
        <div className="flex flex-wrap gap-2">
          <span className="relative inline-flex min-h-9 cursor-pointer items-center justify-center gap-1.5 rounded-md border border-slate-300 bg-white px-3 text-sm font-medium text-slate-800 hover:bg-slate-100 focus-within:ring-2 focus-within:ring-sky-200">
            <Upload size={15} aria-hidden="true" />
            <span>{importLabel}</span>
            <input
              type="file"
              accept=".csv,.tsv,.txt,.xlsx,.xlsm"
              className="absolute inset-0 cursor-pointer opacity-0"
              aria-label={importLabel}
              disabled={busy}
              onChange={(event) => {
                onImport(event.currentTarget.files?.[0]);
                event.currentTarget.value = "";
              }}
            />
          </span>
          <Button type="button" variant="secondary" size="sm" loading={busy} onClick={onExport}>
            <Download size={15} aria-hidden="true" />
            <span>{exportLabel}</span>
          </Button>
        </div>
      </div>
      <div className="max-h-48 overflow-auto rounded-md border border-slate-200 bg-white p-3 text-sm leading-6 text-slate-700">
        {rows.length === 0 ? (
          <p className="text-slate-500">{t("glossary.legacy.empty")}</p>
        ) : (
          <ul className="grid gap-1">
            {rows.map((row, index) => (
              <li key={`${row}-${index}`} className="break-words font-mono text-xs">
                {row}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function FieldToolbar({
  label,
  onExport,
  onImport,
}: {
  label: string;
  onExport: () => void;
  onImport: (file: File | undefined) => void;
}) {
  return (
    <span className="flex flex-wrap items-center justify-between gap-2">
      <span>{label}</span>
      <span className="flex flex-wrap gap-2">
        <Button type="button" variant="secondary" size="sm" onClick={onExport}>
          <Download size={15} aria-hidden="true" />
          <span>{t("glossary.action.exportCsv")}</span>
        </Button>
        <span className="relative inline-flex min-h-9 cursor-pointer items-center justify-center gap-1.5 rounded-md border border-slate-300 bg-white px-3 text-sm font-medium text-slate-800 hover:bg-slate-50 focus-within:ring-2 focus-within:ring-sky-200">
          <Upload size={15} aria-hidden="true" />
          <span>{t("glossary.action.importCsv")}</span>
          <input
            type="file"
            accept=".csv,.tsv,.txt"
            className="absolute inset-0 cursor-pointer opacity-0"
            aria-label={`${label} ${t("glossary.action.importCsv")}`}
            onChange={(event) => {
              onImport(event.currentTarget.files?.[0]);
              event.currentTarget.value = "";
            }}
          />
        </span>
      </span>
    </span>
  );
}
