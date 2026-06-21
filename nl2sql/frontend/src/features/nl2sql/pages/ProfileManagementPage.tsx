import { useEffect, useMemo, useState } from "react";
import { Archive, Plus, RefreshCw, RotateCcw, Save } from "lucide-react";

import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  PageHeader,
  StatusBadge,
} from "@engchina/production-ready-ui";

import { apiGet, apiPatch, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import type { Nl2SqlProfile, ProfileUpsertPayload, SchemaCatalog } from "../types";

interface ProfileFormState {
  name: string;
  description: string;
  allowedTables: string[];
  glossaryText: string;
  sqlRulesText: string;
  defaultRowLimit: number;
  fewShotText: string;
}

const EMPTY_FORM: ProfileFormState = {
  name: "",
  description: "",
  allowedTables: [],
  glossaryText: "",
  sqlRulesText: "SELECT/WITH のみ\nFETCH FIRST で行数制限\n許可された表・列のみ参照",
  defaultRowLimit: 100,
  fewShotText: "",
};

function glossaryToText(glossary: Record<string, string>) {
  return Object.entries(glossary)
    .map(([term, replacement]) => `${term}=${replacement}`)
    .join("\n");
}

function textToGlossary(text: string) {
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
  return examples
    .map((example) => `${example.question ?? ""} => ${example.sql ?? ""}`)
    .join("\n");
}

function textToFewShot(text: string) {
  return lines(text).map((line) => {
    const [question, ...rest] = line.split("=>");
    return { question: question.trim(), sql: rest.join("=>").trim() };
  }).filter((example) => example.question && example.sql);
}

function profileToForm(profile: Nl2SqlProfile): ProfileFormState {
  return {
    name: profile.name,
    description: profile.description,
    allowedTables: profile.allowed_tables,
    glossaryText: glossaryToText(profile.glossary),
    sqlRulesText: profile.sql_rules.join("\n"),
    defaultRowLimit: profile.default_row_limit,
    fewShotText: fewShotToText(profile.few_shot_examples),
  };
}

function formToPayload(form: ProfileFormState): ProfileUpsertPayload {
  return {
    name: form.name.trim(),
    description: form.description.trim(),
    allowed_tables: form.allowedTables,
    glossary: textToGlossary(form.glossaryText),
    sql_rules: lines(form.sqlRulesText),
    default_row_limit: form.defaultRowLimit,
    safety_policy: "select_only",
    few_shot_examples: textToFewShot(form.fewShotText),
  };
}

export function ProfileManagementPage() {
  const [profiles, setProfiles] = useState<Nl2SqlProfile[]>([]);
  const [catalog, setCatalog] = useState<SchemaCatalog | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [form, setForm] = useState<ProfileFormState>(EMPTY_FORM);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  const selectedProfile = useMemo(
    () => profiles.find((profile) => profile.id === selectedId) ?? null,
    [profiles, selectedId]
  );

  const load = async () => {
    setLoading(true);
    setMessage("");
    try {
      const [profileData, catalogData] = await Promise.all([
        apiGet<Nl2SqlProfile[]>("/api/nl2sql/profiles"),
        apiGet<SchemaCatalog>("/api/schema/catalog"),
      ]);
      setProfiles(profileData);
      setCatalog(catalogData);
      if (!selectedId && profileData[0]) {
        setSelectedId(profileData[0].id);
        setForm(profileToForm(profileData[0]));
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("profiles.error.load"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const selectProfile = (profile: Nl2SqlProfile) => {
    setSelectedId(profile.id);
    setForm(profileToForm(profile));
    setMessage("");
  };

  const startNew = () => {
    setSelectedId(null);
    setForm(EMPTY_FORM);
    setMessage("");
  };

  const toggleTable = (tableName: string) => {
    setForm((current) => {
      const selected = current.allowedTables.includes(tableName);
      return {
        ...current,
        allowedTables: selected
          ? current.allowedTables.filter((name) => name !== tableName)
          : [...current.allowedTables, tableName],
      };
    });
  };

  const save = async () => {
    if (!form.name.trim()) {
      setMessage(t("profiles.error.nameRequired"));
      return;
    }
    setSaving(true);
    setMessage("");
    try {
      const payload = formToPayload(form);
      const saved = selectedId
        ? await apiPatch<Nl2SqlProfile>(`/api/nl2sql/profiles/${selectedId}`, payload)
        : await apiPost<Nl2SqlProfile>("/api/nl2sql/profiles", payload);
      await load();
      setSelectedId(saved.id);
      setForm(profileToForm(saved));
      setMessage(t("profiles.message.saved"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("profiles.error.save"));
    } finally {
      setSaving(false);
    }
  };

  const archiveSelected = async () => {
    if (!selectedProfile) return;
    setSaving(true);
    setMessage("");
    try {
      await apiPost<Nl2SqlProfile>(`/api/nl2sql/profiles/${selectedProfile.id}/archive`);
      setSelectedId(null);
      setForm(EMPTY_FORM);
      await load();
      setMessage(t("profiles.message.archived"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("profiles.error.archive"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <PageHeader
        title={t("nav.profiles")}
        subtitle={t("profiles.subtitle")}
        actions={
          <div className="flex flex-wrap gap-2">
            <Button type="button" size="sm" variant="secondary" loading={loading} onClick={() => void load()}>
              <RefreshCw size={15} aria-hidden="true" />
              <span>{t("profiles.action.refresh")}</span>
            </Button>
            <Button type="button" size="sm" variant="primary" onClick={startNew}>
              <Plus size={15} aria-hidden="true" />
              <span>{t("profiles.action.new")}</span>
            </Button>
          </div>
        }
      />

      <main className="grid gap-5 p-4 lg:grid-cols-[minmax(18rem,0.75fr)_minmax(0,1.5fr)] lg:p-8">
        <Card>
          <CardHeader>
            <CardTitle>{t("profiles.list.title")}</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-2">
            {profiles.length === 0 ? (
              <EmptyState title={t("profiles.empty.title")} hint={t("profiles.empty.hint")} />
            ) : (
              profiles.map((profile) => (
                <button
                  key={profile.id}
                  type="button"
                  onClick={() => selectProfile(profile)}
                  className={`grid gap-2 rounded-md border p-3 text-left transition ${
                    selectedId === profile.id
                      ? "border-sky-300 bg-sky-50"
                      : "border-slate-200 bg-white hover:bg-slate-50"
                  }`}
                >
                  <span className="font-medium text-slate-900">{profile.name}</span>
                  <span className="line-clamp-2 text-sm text-slate-600">{profile.description || "-"}</span>
                  <span className="flex flex-wrap gap-2">
                    <StatusBadge variant="neutral" label={t("profiles.list.tables", { count: profile.allowed_tables.length })} />
                    <StatusBadge variant="neutral" label={t("profiles.list.rules", { count: profile.sql_rules.length })} />
                  </span>
                </button>
              ))
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{selectedProfile ? t("profiles.editor.edit") : t("profiles.editor.new")}</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-5">
            {message && (
              <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700" role="status">
                {message}
              </div>
            )}

            <div className="grid gap-4 md:grid-cols-[1fr_10rem]">
              <label className="grid gap-1 text-sm font-medium text-slate-800">
                <span>{t("profiles.field.name")}</span>
                <input
                  value={form.name}
                  onChange={(event) => {
                    const value = event.currentTarget.value;
                    setForm((current) => ({ ...current, name: value }));
                  }}
                  className="min-h-11 rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                />
              </label>
              <label className="grid gap-1 text-sm font-medium text-slate-800">
                <span>{t("profiles.field.rowLimit")}</span>
                <input
                  type="number"
                  min={1}
                  max={5000}
                  value={form.defaultRowLimit}
                  onChange={(event) => {
                    const value = Number(event.currentTarget.value) || 100;
                    setForm((current) => ({ ...current, defaultRowLimit: value }));
                  }}
                  className="min-h-11 rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                />
              </label>
            </div>

            <label className="grid gap-1 text-sm font-medium text-slate-800">
              <span>{t("profiles.field.description")}</span>
              <textarea
                value={form.description}
                rows={3}
                onChange={(event) => {
                  const value = event.currentTarget.value;
                  setForm((current) => ({ ...current, description: value }));
                }}
                className="rounded-md border border-slate-300 px-3 py-2 text-sm leading-6 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
              />
            </label>

            <section className="grid gap-2">
              <h3 className="text-sm font-semibold text-slate-900">{t("profiles.field.allowedTables")}</h3>
              <div className="grid gap-2 md:grid-cols-2">
                {catalog?.tables.map((table) => (
                  <label key={table.table_name} className="flex min-h-11 items-start gap-2 rounded-md border border-slate-200 p-3 text-sm">
                    <input
                      type="checkbox"
                      checked={form.allowedTables.includes(table.table_name)}
                      onChange={() => toggleTable(table.table_name)}
                      className="mt-1 h-4 w-4 rounded border-slate-300"
                    />
                    <span>
                      <span className="font-medium text-slate-900">{table.logical_name}</span>
                      <span className="ml-2 font-mono text-xs text-slate-500">{table.table_name}</span>
                      <span className="mt-1 block text-xs text-slate-500">{table.comment}</span>
                    </span>
                  </label>
                ))}
              </div>
            </section>

            <div className="grid gap-4 md:grid-cols-2">
              <label className="grid gap-1 text-sm font-medium text-slate-800">
                <span>{t("profiles.field.glossary")}</span>
                <textarea
                  value={form.glossaryText}
                  rows={6}
                  placeholder={t("profiles.placeholder.glossary")}
                  onChange={(event) => {
                    const value = event.currentTarget.value;
                    setForm((current) => ({ ...current, glossaryText: value }));
                  }}
                  className="rounded-md border border-slate-300 px-3 py-2 font-mono text-sm leading-6 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                />
              </label>
              <label className="grid gap-1 text-sm font-medium text-slate-800">
                <span>{t("profiles.field.rules")}</span>
                <textarea
                  value={form.sqlRulesText}
                  rows={6}
                  onChange={(event) => {
                    const value = event.currentTarget.value;
                    setForm((current) => ({ ...current, sqlRulesText: value }));
                  }}
                  className="rounded-md border border-slate-300 px-3 py-2 text-sm leading-6 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                />
              </label>
            </div>

            <label className="grid gap-1 text-sm font-medium text-slate-800">
              <span>{t("profiles.field.fewShot")}</span>
              <textarea
                value={form.fewShotText}
                rows={4}
                placeholder={t("profiles.placeholder.fewShot")}
                onChange={(event) => {
                  const value = event.currentTarget.value;
                  setForm((current) => ({ ...current, fewShotText: value }));
                }}
                className="rounded-md border border-slate-300 px-3 py-2 font-mono text-sm leading-6 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
              />
            </label>

            <div className="flex flex-wrap justify-between gap-2">
              <Button type="button" variant="secondary" disabled={saving} onClick={startNew}>
                <RotateCcw size={16} aria-hidden="true" />
                <span>{t("profiles.action.reset")}</span>
              </Button>
              <div className="flex flex-wrap gap-2">
                <Button type="button" variant="danger" disabled={!selectedProfile || saving} onClick={() => void archiveSelected()}>
                  <Archive size={16} aria-hidden="true" />
                  <span>{t("profiles.action.archive")}</span>
                </Button>
                <Button type="button" variant="primary" loading={saving} onClick={() => void save()}>
                  <Save size={16} aria-hidden="true" />
                  <span>{t("profiles.action.save")}</span>
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      </main>
    </>
  );
}
