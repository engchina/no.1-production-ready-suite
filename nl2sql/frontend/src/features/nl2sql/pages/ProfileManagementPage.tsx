import { useEffect, useMemo, useState } from "react";
import {
  Archive,
  ArchiveRestore,
  ArrowDownUp,
  Database,
  FileJson,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Trash2,
  UserCog,
  X,
} from "lucide-react";

import {
  Button,
  EmptyState,
  PageHeader,
  StatusBadge,
  ToggleChip,
  toast,
} from "@engchina/production-ready-ui";

import { apiGet, apiPatch, apiPost } from "@/lib/api";
import { FixedSplitPane } from "@/components/layout/FixedSplitPane";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import {
  ExecutionConfirmationField,
  SelectionListPanel,
} from "../components/DbAdminShared";
import {
  DbManagementSearchField,
  DbObjectManagementPanelShell,
  DbObjectManagementStatusBar,
  DbObjectManagementTabs,
  DbObjectPanelHeader,
  type DbObjectTab,
} from "../components/DbObjectManagementShared";
import type {
  DbAdminObjectsData,
  Nl2SqlProfile,
  ProfileSelectAiConfig,
  ProfileUpsertPayload,
  SchemaCatalog,
  SelectAiDbProfile,
  SelectAiDbProfileMutationData,
  SelectAiDbProfilesData,
} from "../types";

type ActiveView = "list" | "create" | "oracle";
type ProfileStatusFilter = "active" | "archived";
type SortKey = "name" | "tables" | "views";
type SortDirection = "asc" | "desc";

const PROFILE_MANAGEMENT_ID = "profile-management";
const BUSINESS_SELECT_AI_DB_PROFILES_DETAIL_URL =
  "/api/nl2sql/select-ai/db-profiles?include_detail=true&business_profiles_only=true&include_archived_business_profiles=true";

interface SortState {
  key: SortKey;
  direction: SortDirection;
}

interface ProfileFormState {
  name: string;
  category: string;
  description: string;
  allowedTables: string[];
  allowedViews: string[];
  glossaryText: string;
  sqlRulesText: string;
  defaultRowLimit: number;
  fewShotText: string;
  selectAiConfig: ProfileSelectAiConfig;
}

const DEFAULT_SELECT_AI_CONFIG: ProfileSelectAiConfig = {
  profile_name: "",
  region: "",
  model: "",
  embedding_model: "cohere.embed-v4.0",
  max_tokens: 32000,
  enforce_object_list: true,
  comments: true,
  annotations: false,
  constraints: false,
  role: "",
  additional_instructions: "",
};

const EMPTY_FORM: ProfileFormState = {
  name: "",
  category: "",
  description: "",
  allowedTables: [],
  allowedViews: [],
  glossaryText: "",
  sqlRulesText: "SELECT/WITH のみ\nFETCH FIRST で行数制限\n許可された表・列のみ参照",
  defaultRowLimit: 100,
  fewShotText: "",
  selectAiConfig: DEFAULT_SELECT_AI_CONFIG,
};

function emptyProfileForm(): ProfileFormState {
  return {
    ...EMPTY_FORM,
    allowedTables: [],
    allowedViews: [],
    selectAiConfig: { ...DEFAULT_SELECT_AI_CONFIG },
  };
}

const inputClass =
  "min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200";
const textareaClass =
  "rounded-md border border-slate-300 bg-white px-3 py-2 text-sm leading-6 outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200";

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

function normalizeProfile(profile: Nl2SqlProfile): Nl2SqlProfile {
  return {
    ...profile,
    allowed_views: profile.allowed_views ?? [],
    select_ai_config: { ...DEFAULT_SELECT_AI_CONFIG, ...(profile.select_ai_config ?? {}) },
  };
}

function profileToForm(profile: Nl2SqlProfile): ProfileFormState {
  const normalized = normalizeProfile(profile);
  return {
    name: normalized.name,
    category: normalized.category ?? "",
    description: normalized.description,
    allowedTables: normalized.allowed_tables,
    allowedViews: normalized.allowed_views,
    glossaryText: glossaryToText(normalized.glossary),
    sqlRulesText: normalized.sql_rules.join("\n"),
    defaultRowLimit: normalized.default_row_limit,
    fewShotText: fewShotToText(normalized.few_shot_examples),
    selectAiConfig: normalized.select_ai_config,
  };
}

function formToPayload(form: ProfileFormState): ProfileUpsertPayload {
  return {
    name: form.name.trim(),
    category: form.category.trim(),
    description: form.description.trim(),
    allowed_tables: form.allowedTables,
    allowed_views: form.allowedViews,
    glossary: textToGlossary(form.glossaryText),
    sql_rules: lines(form.sqlRulesText),
    default_row_limit: form.defaultRowLimit,
    safety_policy: "select_only",
    few_shot_examples: textToFewShot(form.fewShotText),
    select_ai_config: {
      ...form.selectAiConfig,
      profile_name: form.selectAiConfig.profile_name.trim(),
      region: form.selectAiConfig.region.trim(),
      model: form.selectAiConfig.model.trim(),
      embedding_model: form.selectAiConfig.embedding_model.trim() || "cohere.embed-v4.0",
      max_tokens: Number(form.selectAiConfig.max_tokens) || 32000,
      role: form.selectAiConfig.role.trim(),
      additional_instructions: form.selectAiConfig.additional_instructions.trim(),
    },
  };
}

function profileSortValue(profile: Nl2SqlProfile, key: SortKey) {
  if (key === "tables") return profile.allowed_tables.length;
  if (key === "views") return (profile.allowed_views ?? []).length;
  return profile.name.toLowerCase();
}

function SortButton({
  label,
  sortKey,
  sort,
  onToggle,
}: {
  label: string;
  sortKey: SortKey;
  sort: SortState;
  onToggle: (key: SortKey) => void;
}) {
  const active = sort.key === sortKey;
  return (
    <button
      type="button"
      className="inline-flex items-center gap-1 whitespace-nowrap text-left font-semibold text-slate-700 hover:text-slate-950 focus:outline-none focus:ring-2 focus:ring-sky-200"
      aria-sort={active ? (sort.direction === "asc" ? "ascending" : "descending") : "none"}
      onClick={() => onToggle(sortKey)}
    >
      <span>{label}</span>
      <ArrowDownUp size={13} className={active ? "text-sky-700" : "text-slate-400"} aria-hidden="true" />
    </button>
  );
}

function profileObjectList(profile: SelectAiDbProfile) {
  const explicit = [...(profile.tables ?? []), ...(profile.views ?? [])].filter(Boolean);
  if (explicit.length > 0) return explicit.join(", ");
  return (profile.object_list ?? [])
    .map((item) => {
      if (typeof item === "string") return item;
      return String(item.name ?? "");
    })
    .filter(Boolean)
    .join(", ");
}

function profileAttributesSql(profileName: string, description: string) {
  const escapedName = profileName.replace(/'/g, "''");
  const escapedDescription = description.replace(/'/g, "''");
  return [
    `BEGIN DBMS_CLOUD_AI.DROP_PROFILE(profile_name => '${escapedName}'); EXCEPTION WHEN OTHERS THEN NULL; END;`,
    `BEGIN DBMS_CLOUD_AI.CREATE_PROFILE(profile_name => '${escapedName}', attributes => :attrs, description => '${escapedDescription}'); END;`,
  ].join("\n");
}

function updateSelectAiConfig(
  setForm: (updater: (current: ProfileFormState) => ProfileFormState) => void,
  patch: Partial<ProfileSelectAiConfig>
) {
  setForm((current) => ({
    ...current,
    selectAiConfig: { ...current.selectAiConfig, ...patch },
  }));
}

function StatusBar({
  profileCount,
  objectCount,
  oracleProfileCount,
  runtime,
  loading,
  onRefresh,
}: {
  profileCount: number;
  objectCount: number;
  oracleProfileCount: number;
  runtime: string;
  loading: boolean;
  onRefresh: () => void;
}) {
  return (
    <DbObjectManagementStatusBar
      ariaLabel={t("profiles.status.label")}
      metricColumnsClass="sm:grid-cols-3 lg:grid-cols-4"
      metrics={[
        { label: t("profiles.metric.profiles"), value: formatNumber(profileCount), emphasis: true },
        { label: t("profiles.metric.objects"), value: formatNumber(objectCount), emphasis: true },
        { label: t("profiles.metric.oracleProfiles"), value: formatNumber(oracleProfileCount), emphasis: true },
        { label: t("profiles.oracle.runtime"), value: runtime },
      ]}
      actions={
        <Button type="button" variant="secondary" size="sm" loading={loading} onClick={onRefresh}>
          <RefreshCw size={15} aria-hidden="true" />
          <span>{t("profiles.action.refresh")}</span>
        </Button>
      }
    />
  );
}

function ProfileList({
  profiles,
  selectedId,
  loading,
  search,
  statusFilter,
  sort,
  onSearchChange,
  onStatusFilterChange,
  onSortChange,
  onSelect,
}: {
  profiles: Nl2SqlProfile[];
  selectedId: string | null;
  loading: boolean;
  search: string;
  statusFilter: ProfileStatusFilter;
  sort: SortState;
  onSearchChange: (value: string) => void;
  onStatusFilterChange: (value: ProfileStatusFilter) => void;
  onSortChange: (key: SortKey) => void;
  onSelect: (profile: Nl2SqlProfile) => void;
}) {
  return (
    <section className="grid min-w-0 content-start gap-3" aria-labelledby="profile-list-heading">
      <DbObjectPanelHeader
        headingId="profile-list-heading"
        icon={UserCog}
        title={t("profiles.list.title")}
        description={t("profiles.list.hint")}
        action={<StatusBadge variant="info" label={t("profiles.objects.count", { count: profiles.length })} />}
      />
      <div
        className="flex flex-wrap items-center gap-1"
        role="group"
        aria-label={t("profiles.filter.statusLabel")}
      >
        <ToggleChip
          selected={statusFilter === "active"}
          onClick={() => onStatusFilterChange("active")}
        >
          {t("profiles.filter.active")}
        </ToggleChip>
        <ToggleChip
          selected={statusFilter === "archived"}
          onClick={() => onStatusFilterChange("archived")}
        >
          {t("profiles.filter.archived")}
        </ToggleChip>
      </div>
      <div className="grid gap-2 rounded-md border border-slate-200 bg-slate-50 p-3">
        <DbManagementSearchField
          label={t("profiles.list.search")}
          placeholder={t("profiles.list.searchPlaceholder")}
          value={search}
          onChange={onSearchChange}
        />
      </div>
      {loading ? (
        <div className="grid gap-2" data-testid="profile-list-skeleton">
          {Array.from({ length: 6 }, (_, index) => (
            <div key={index} className="h-12 animate-pulse rounded-md bg-slate-100" />
          ))}
        </div>
      ) : profiles.length === 0 ? (
        <EmptyState
          title={
            search.trim()
              ? t("profiles.list.noResultsTitle")
              : statusFilter === "archived"
                ? t("profiles.empty.archivedTitle")
                : t("profiles.empty.title")
          }
          hint={
            search.trim()
              ? t("profiles.list.noResultsHint")
              : statusFilter === "archived"
                ? t("profiles.empty.archivedHint")
                : t("profiles.empty.hint")
          }
        />
      ) : (
        <div className="overflow-hidden rounded-md border border-slate-200 bg-white">
          <div className="max-h-[42rem] overflow-auto" data-testid="profile-management-list">
            <table className="w-full min-w-[32rem] table-fixed divide-y divide-slate-200 text-left text-sm" data-testid="profile-management-grid">
              <colgroup>
                <col />
                <col className="w-[5rem]" />
                <col className="w-[5rem]" />
                <col className="w-[7rem]" />
              </colgroup>
              <thead className="sticky top-0 z-10 bg-slate-50 text-xs text-slate-600">
                <tr>
                  <th className="px-3 py-2">
                    <SortButton label={t("profiles.field.name")} sortKey="name" sort={sort} onToggle={onSortChange} />
                  </th>
                  <th className="px-3 py-2">
                    <SortButton label={t("profiles.field.allowedTables")} sortKey="tables" sort={sort} onToggle={onSortChange} />
                  </th>
                  <th className="px-3 py-2">
                    <SortButton label={t("profiles.field.allowedViews")} sortKey="views" sort={sort} onToggle={onSortChange} />
                  </th>
                  <th className="px-3 py-2 text-right">{t("tableMgmt.grid.actions")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {profiles.map((profile) => {
                  const selected = profile.id === selectedId;
                  return (
                    <tr key={profile.id} className={selected ? "bg-sky-50" : "hover:bg-slate-50"}>
                      <td className="px-3 py-2 align-top">
                        <button
                          type="button"
                          className="grid max-w-full text-left focus:outline-none focus:ring-2 focus:ring-sky-200"
                          aria-current={selected ? "true" : undefined}
                          onClick={() => onSelect(profile)}
                        >
                          <span className="break-words font-semibold text-sky-900">{profile.name}</span>
                          <span className="line-clamp-2 text-xs leading-5 text-slate-600">{profile.description || "-"}</span>
                        </button>
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-slate-700">{profile.allowed_tables.length}</td>
                      <td className="px-3 py-2 font-mono text-xs text-slate-700">{(profile.allowed_views ?? []).length}</td>
                      <td className="px-3 py-2 text-right">
                        <Button type="button" variant="secondary" size="sm" onClick={() => onSelect(profile)}>
                          <span>{t("profiles.action.select")}</span>
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}

function SelectAiConfigFields({
  form,
  setForm,
}: {
  form: ProfileFormState;
  setForm: (updater: (current: ProfileFormState) => ProfileFormState) => void;
}) {
  return (
    <section className="grid gap-3 rounded-md border border-slate-200 bg-slate-50 p-3" aria-label={t("profiles.editor.selectAi")}>
      <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_10rem_10rem]">
        <label className="grid gap-1 text-sm font-medium text-slate-800">
          <span>{t("profiles.field.profileName")}</span>
          <input
            value={form.selectAiConfig.profile_name}
            placeholder={t("profiles.placeholder.profileName")}
            onChange={(event) => updateSelectAiConfig(setForm, { profile_name: event.currentTarget.value })}
            className={inputClass}
          />
        </label>
        <label className="grid gap-1 text-sm font-medium text-slate-800">
          <span>{t("profiles.field.region")}</span>
          <input
            value={form.selectAiConfig.region}
            placeholder="ap-osaka-1"
            onChange={(event) => updateSelectAiConfig(setForm, { region: event.currentTarget.value })}
            className={inputClass}
          />
        </label>
        <label className="grid gap-1 text-sm font-medium text-slate-800">
          <span>{t("profiles.field.maxTokens")}</span>
          <input
            type="number"
            min={1}
            max={128000}
            value={form.selectAiConfig.max_tokens}
            onChange={(event) => updateSelectAiConfig(setForm, { max_tokens: Number(event.currentTarget.value) || 32000 })}
            className={inputClass}
          />
        </label>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="grid gap-1 text-sm font-medium text-slate-800">
          <span>{t("profiles.field.model")}</span>
          <input
            value={form.selectAiConfig.model}
            onChange={(event) => updateSelectAiConfig(setForm, { model: event.currentTarget.value })}
            className={inputClass}
          />
        </label>
        <label className="grid gap-1 text-sm font-medium text-slate-800">
          <span>{t("profiles.field.embeddingModel")}</span>
          <input
            value={form.selectAiConfig.embedding_model}
            onChange={(event) => updateSelectAiConfig(setForm, { embedding_model: event.currentTarget.value })}
            className={inputClass}
          />
        </label>
      </div>
      <div className="grid gap-2 md:grid-cols-4">
        {([
          ["enforce_object_list", "profiles.field.enforceObjectList"],
          ["comments", "profiles.field.comments"],
          ["annotations", "profiles.field.annotations"],
          ["constraints", "profiles.field.constraints"],
        ] as const).map(([key, labelKey]) => (
          <label key={key} className="flex min-h-11 items-center gap-2 rounded-md border border-slate-200 bg-white p-3 text-sm text-slate-800">
            <input
              type="checkbox"
              checked={Boolean(form.selectAiConfig[key])}
              onChange={(event) => updateSelectAiConfig(setForm, { [key]: event.currentTarget.checked })}
              className="h-4 w-4 rounded border-slate-300 text-sky-700 focus:ring-sky-500"
            />
            <span>{t(labelKey)}</span>
          </label>
        ))}
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <div className="grid content-start gap-1 text-sm font-medium text-slate-800">
          <label htmlFor="profile-select-ai-role">{t("profiles.field.role")}</label>
          <textarea
            id="profile-select-ai-role"
            aria-describedby="profile-select-ai-role-hint"
            value={form.selectAiConfig.role}
            rows={6}
            onChange={(event) => updateSelectAiConfig(setForm, { role: event.currentTarget.value })}
            className={`${textareaClass} min-h-40`}
            placeholder={t("profiles.placeholder.role")}
          />
          <p id="profile-select-ai-role-hint" className="text-xs font-normal leading-5 text-slate-600">
            {t("profiles.field.roleHint")}
          </p>
        </div>
        <div className="grid content-start gap-1 text-sm font-medium text-slate-800">
          <label htmlFor="profile-select-ai-additional-instructions">
            {t("profiles.field.additionalInstructions")}
          </label>
          <textarea
            id="profile-select-ai-additional-instructions"
            aria-describedby="profile-select-ai-additional-instructions-hint"
            value={form.selectAiConfig.additional_instructions}
            rows={6}
            onChange={(event) =>
              updateSelectAiConfig(setForm, {
                additional_instructions: event.currentTarget.value,
              })
            }
            className={`${textareaClass} min-h-40`}
            placeholder={t("profiles.placeholder.additionalInstructions")}
          />
          <p
            id="profile-select-ai-additional-instructions-hint"
            className="text-xs font-normal leading-5 text-slate-600"
          >
            {t("profiles.field.additionalInstructionsHint")}
          </p>
        </div>
      </div>
    </section>
  );
}

function ProfileEditor({
  selectedProfile,
  form,
  tableNames,
  viewNames,
  objectFilter,
  saving,
  oraclePreview,
  oracleConfirmation,
  oracleLoading,
  onObjectFilterChange,
  onFormChange,
  onToggleTable,
  onToggleView,
  onSave,
  onArchive,
  onRestore,
  onReset,
  onOracleExecute,
  onOracleConfirmationChange,
}: {
  selectedProfile: Nl2SqlProfile | null;
  form: ProfileFormState;
  tableNames: string[];
  viewNames: string[];
  objectFilter: string;
  saving: boolean;
  oraclePreview: SelectAiDbProfileMutationData | null;
  oracleConfirmation: string;
  oracleLoading: boolean;
  onObjectFilterChange: (value: string) => void;
  onFormChange: (updater: (current: ProfileFormState) => ProfileFormState) => void;
  onToggleTable: (name: string) => void;
  onToggleView: (name: string) => void;
  onSave: () => void;
  onArchive: () => void;
  onRestore: () => void;
  onReset: () => void;
  onOracleExecute: () => void;
  onOracleConfirmationChange: (value: string) => void;
}) {
  const oracleConfirmed = oracleConfirmation.trim() === "ADMIN_EXECUTE";
  const archived = selectedProfile?.archived === true;
  return (
    <section className="grid min-w-0 content-start gap-4" aria-labelledby="profile-editor-heading">
      <DbObjectPanelHeader
        headingId="profile-editor-heading"
        icon={FileJson}
        title={selectedProfile ? t("profiles.editor.edit") : t("profiles.editor.new")}
        description={selectedProfile?.name ?? t("profiles.empty.hint")}
        action={
          <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
            {archived ? (
              <>
                <StatusBadge variant="neutral" label={t("profiles.status.archived")} />
                <Button type="button" variant="primary" size="sm" loading={saving} onClick={onRestore}>
                  <ArchiveRestore size={15} aria-hidden="true" />
                  <span>{t("profiles.action.restore")}</span>
                </Button>
              </>
            ) : (
              <>
                <Button type="button" variant="secondary" size="sm" disabled={saving} onClick={onReset}>
                  <RotateCcw size={15} aria-hidden="true" />
                  <span>{t("profiles.action.reset")}</span>
                </Button>
                <Button type="button" variant="danger" size="sm" disabled={!selectedProfile || saving} onClick={onArchive}>
                  <Archive size={15} aria-hidden="true" />
                  <span>{t("profiles.action.archive")}</span>
                </Button>
                <Button type="button" variant="primary" size="sm" loading={saving} onClick={onSave}>
                  <Save size={15} aria-hidden="true" />
                  <span>{t("profiles.action.save")}</span>
                </Button>
              </>
            )}
          </div>
        }
      />

      <fieldset disabled={archived} className="contents">
      <section className="grid gap-3 rounded-md border border-slate-200 bg-slate-50 p-3">
        <h3 className="text-sm font-semibold text-slate-900">{t("profiles.editor.basic")}</h3>
        <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_14rem_10rem]">
          <label className="grid gap-1 text-sm font-medium text-slate-800">
            <span>{t("profiles.field.name")}</span>
            <input
              value={form.name}
              onChange={(event) => {
                const value = event.currentTarget.value;
                onFormChange((current) => ({ ...current, name: value }));
              }}
              className={inputClass}
            />
          </label>
          <label className="grid gap-1 text-sm font-medium text-slate-800">
            <span>{t("profiles.field.category")}</span>
            <input
              value={form.category}
              onChange={(event) => {
                const value = event.currentTarget.value;
                onFormChange((current) => ({ ...current, category: value }));
              }}
              className={inputClass}
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
                onFormChange((current) => ({ ...current, defaultRowLimit: value }));
              }}
              className={inputClass}
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
              onFormChange((current) => ({ ...current, description: value }));
            }}
            className={textareaClass}
          />
        </label>
      </section>

      <section className="grid gap-3 rounded-md border border-slate-200 bg-slate-50 p-3" data-testid="profile-allowed-object-list">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">{t("profiles.editor.objects")}</h3>
          <p className="mt-1 text-sm text-slate-600">{t("profiles.field.allowedObjects")}</p>
        </div>
        <div
          className="grid gap-2 rounded-md border border-slate-200 bg-slate-50 p-3"
          data-testid="profile-object-search-toolbar"
        >
          <div className="w-full md:max-w-2xl">
            <DbManagementSearchField
              label={t("profiles.objects.filter")}
              placeholder={t("profiles.objects.filterPlaceholder")}
              value={objectFilter}
              onChange={onObjectFilterChange}
            />
          </div>
        </div>
        <div className="grid gap-3 xl:grid-cols-2">
          <SelectionListPanel
            title={t("profiles.objects.tablesTitle")}
            items={tableNames}
            selectedItems={form.allowedTables}
            selectedCountLabel={t("profiles.objects.selected", { count: form.allowedTables.length })}
            dataTestId="profile-allowed-table-list"
            ariaLabel={t("profiles.objects.tablesTitle")}
            emptyTitle={t("profiles.objects.emptyTables")}
            emptyHint={t("profiles.objects.emptyTablesHint")}
            onToggle={onToggleTable}
          />
          <SelectionListPanel
            title={t("profiles.objects.viewsTitle")}
            items={viewNames}
            selectedItems={form.allowedViews}
            selectedCountLabel={t("profiles.objects.selected", { count: form.allowedViews.length })}
            dataTestId="profile-allowed-view-list"
            ariaLabel={t("profiles.objects.viewsTitle")}
            emptyTitle={t("profiles.objects.emptyViews")}
            emptyHint={t("profiles.objects.emptyViewsHint")}
            onToggle={onToggleView}
          />
        </div>
      </section>

      <section className="grid gap-3 rounded-md border border-slate-200 bg-slate-50 p-3">
        <h3 className="text-sm font-semibold text-slate-900">{t("profiles.editor.learning")}</h3>
        <div className="grid gap-3 lg:grid-cols-2">
          <label className="grid gap-1 text-sm font-medium text-slate-800">
            <span>{t("profiles.field.glossary")}</span>
            <textarea
              value={form.glossaryText}
              rows={6}
              placeholder={t("profiles.placeholder.glossary")}
              onChange={(event) => {
                const value = event.currentTarget.value;
                onFormChange((current) => ({ ...current, glossaryText: value }));
              }}
              className={`${textareaClass} font-mono`}
            />
          </label>
          <label className="grid gap-1 text-sm font-medium text-slate-800">
            <span>{t("profiles.field.rules")}</span>
            <textarea
              value={form.sqlRulesText}
              rows={6}
              onChange={(event) => {
                const value = event.currentTarget.value;
                onFormChange((current) => ({ ...current, sqlRulesText: value }));
              }}
              className={textareaClass}
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
              onFormChange((current) => ({ ...current, fewShotText: value }));
            }}
            className={`${textareaClass} font-mono`}
          />
        </label>
      </section>

      <SelectAiConfigFields form={form} setForm={onFormChange} />

      <section className="grid gap-3 rounded-md border border-slate-200 bg-white p-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h3 className="text-sm font-semibold text-slate-900">{t("profiles.editor.oraclePreview")}</h3>
            <p className="mt-1 text-sm text-slate-600">{t("profiles.oracle.hint")}</p>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:justify-end">
            <Button
              type="button"
              variant="primary"
              size="sm"
              loading={oracleLoading}
              disabled={!selectedProfile || !oracleConfirmed}
              onClick={onOracleExecute}
            >
              <Database size={15} aria-hidden="true" />
              <span>{t("profiles.action.executeOracle")}</span>
            </Button>
          </div>
        </div>
        {oraclePreview ? (
          <OracleMutationResult result={oraclePreview} />
        ) : (
          <div className="rounded-md border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500">
            {t("profiles.oracle.previewEmpty")}
          </div>
        )}
        <ExecutionConfirmationField
          value={oracleConfirmation}
          onChange={onOracleConfirmationChange}
          confirmed={oracleConfirmed}
          placeholder="ADMIN_EXECUTE"
          expectedLabel="ADMIN_EXECUTE"
          helper={t("profiles.oracle.executeHint")}
        />
      </section>
      </fieldset>
    </section>
  );
}

function OracleMutationResult({ result }: { result: SelectAiDbProfileMutationData }) {
  return (
    <section className="grid gap-2 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm" data-testid="profile-oracle-result">
      <div className="flex flex-wrap gap-2">
        <StatusBadge variant={result.executed ? "success" : result.status === "error" ? "danger" : "neutral"} label={result.status} />
        <StatusBadge variant="neutral" label={result.runtime} />
        {result.profile_name && <StatusBadge variant="info" label={result.profile_name} />}
      </div>
      {result.warnings.map((warning) => (
        <p key={warning} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-amber-950">
          {warning}
        </p>
      ))}
      {result.ddl.length > 0 && (
        <pre className="overflow-auto rounded-md border border-slate-200 bg-white p-3 font-mono text-xs leading-5 text-slate-800">
          <code>{result.ddl.join("\n")}</code>
        </pre>
      )}
      {result.profile && (
        <pre className="max-h-72 overflow-auto rounded-md border border-slate-200 bg-slate-950 p-3 font-mono text-xs leading-5 text-slate-50">
          <code>{JSON.stringify(result.profile.attributes ?? {}, null, 2)}</code>
        </pre>
      )}
    </section>
  );
}

function OracleProfilesPanel({
  dbProfiles,
  selectedProfileName,
  selectedProfile,
  profileJson,
  dropTargetName,
  dropConfirmation,
  loading,
  onSelect,
  onJsonChange,
  onSaveJson,
  onOpenDrop,
  onCloseDrop,
  onDropConfirmationChange,
  onDropExecute,
}: {
  dbProfiles: SelectAiDbProfilesData | null;
  selectedProfileName: string;
  selectedProfile: SelectAiDbProfile | null;
  profileJson: string;
  dropTargetName: string;
  dropConfirmation: string;
  loading: string;
  onSelect: (profile: SelectAiDbProfile) => void;
  onJsonChange: (value: string) => void;
  onSaveJson: () => void;
  onOpenDrop: (name: string) => void;
  onCloseDrop: () => void;
  onDropConfirmationChange: (value: string) => void;
  onDropExecute: () => void;
}) {
  const dropConfirmed = dropConfirmation.trim() === dropTargetName || dropConfirmation.trim() === "ADMIN_EXECUTE";
  const selectedDescription = selectedProfile?.description || selectedProfile?.category || "";
  return (
    <>
      <FixedSplitPane
        splitId="profile-management-oracle"
        preferredWidePane="right"
        left={
          <section className="grid min-w-0 content-start gap-3">
        <DbObjectPanelHeader
          icon={Database}
          title={t("profiles.oracle.list")}
          description={t("profiles.oracle.hint")}
          action={<StatusBadge variant="neutral" label={dbProfiles?.runtime ?? "deterministic"} />}
        />
        <div className="overflow-hidden rounded-md border border-slate-200 bg-white">
          <div className="max-h-[42rem] overflow-auto" data-testid="profile-oracle-list">
            {(dbProfiles?.profiles ?? []).length === 0 ? (
              <div className="grid min-h-28 place-items-center p-4 text-sm text-slate-500">{t("profiles.oracle.empty")}</div>
            ) : (
              <table className="w-full min-w-[34rem] table-fixed divide-y divide-slate-200 text-left text-sm">
                <colgroup>
                  <col className="w-[12rem]" />
                  <col className="w-[6.5rem]" />
                  <col className="w-[14rem]" />
                </colgroup>
                <thead className="sticky top-0 z-10 bg-slate-50 text-xs text-slate-600">
                  <tr>
                    <th className="px-3 py-2">{t("profiles.field.profileName")}</th>
                    <th className="px-3 py-2">{t("profiles.oracle.runtime")}</th>
                    <th className="px-3 py-2 text-right">{t("tableMgmt.grid.actions")}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {(dbProfiles?.profiles ?? []).map((profile) => {
                    const selected = profile.name === selectedProfileName;
                    return (
                      <tr key={profile.name} className={selected ? "bg-sky-50" : "hover:bg-slate-50"}>
                        <td className="px-3 py-2 align-top">
                          <button
                            type="button"
                            className="grid w-full min-w-0 max-w-full text-left focus:outline-none focus:ring-2 focus:ring-sky-200"
                            aria-current={selected ? "true" : undefined}
                            onClick={() => onSelect(profile)}
                          >
                            <span className="block w-full min-w-0 whitespace-normal break-all font-mono text-xs font-semibold leading-5 text-sky-900">
                              {profile.name}
                            </span>
                            <span className="line-clamp-2 text-xs leading-5 text-slate-600">{profileObjectList(profile) || "-"}</span>
                          </button>
                        </td>
                        <td className="px-3 py-2">
                          <StatusBadge variant="neutral" label={profile.status || "-"} />
                        </td>
                        <td className="px-3 py-2 text-right">
                          <div className="flex flex-nowrap items-center justify-end gap-2">
                            <Button type="button" variant="secondary" size="sm" className="shrink-0 whitespace-nowrap" onClick={() => onSelect(profile)}>
                              <FileJson size={15} aria-hidden="true" />
                              <span>{t("engineOps.dbProfiles.detail")}</span>
                            </Button>
                            <Button type="button" variant="danger" size="sm" className="shrink-0 whitespace-nowrap" onClick={() => onOpenDrop(profile.name)}>
                              <Trash2 size={15} aria-hidden="true" />
                              <span>{t("profiles.oracle.drop")}</span>
                            </Button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </div>
        {dbProfiles?.warnings.map((warning) => (
          <p key={warning} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-950">
            {warning}
          </p>
        ))}
          </section>
        }
        right={
          <section className="grid min-w-0 content-start gap-3 rounded-md border border-slate-200 bg-slate-50 p-4" aria-label={t("profiles.oracle.detail")}>
        <DbObjectPanelHeader
          icon={FileJson}
          title={selectedProfile?.name || t("profiles.oracle.detail")}
          description={selectedProfile ? selectedDescription || profileObjectList(selectedProfile) || "-" : t("profiles.oracle.noSelection")}
          action={
            <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
              <Button type="button" variant="primary" size="sm" disabled={!profileJson.trim()} loading={loading === "oracle-save"} onClick={onSaveJson}>
                <Database size={15} aria-hidden="true" />
                <span>{t("profiles.oracle.saveExecute")}</span>
              </Button>
            </div>
          }
        />
        {selectedProfile ? (
          <>
            <div className="grid gap-2 md:grid-cols-3">
              <StatusBadge variant="neutral" label={selectedProfile.region || "-"} />
              <StatusBadge variant="neutral" label={selectedProfile.model || "-"} />
              <StatusBadge variant="neutral" label={selectedProfile.embedding_model || "-"} />
            </div>
            <label className="grid gap-1 text-sm font-medium text-slate-800">
              <span>{t("profiles.oracle.json")}</span>
              <textarea
                value={profileJson}
                onChange={(event) => onJsonChange(event.currentTarget.value)}
                rows={14}
                className={`${textareaClass} min-h-80 font-mono text-xs`}
              />
            </label>
            <section className="grid gap-2">
              <h3 className="text-sm font-semibold text-slate-900">{t("profiles.oracle.sql")}</h3>
              <pre className="overflow-auto rounded-md border border-slate-200 bg-white p-3 font-mono text-xs leading-5 text-slate-800">
                <code>{profileAttributesSql(selectedProfile.name, selectedDescription)}</code>
              </pre>
            </section>
          </>
        ) : (
          <EmptyState title={t("profiles.oracle.noSelection")} hint={t("profiles.oracle.hint")} />
        )}
          </section>
        }
      />

      {dropTargetName && (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/50 p-3 sm:items-center" role="presentation">
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="profile-oracle-drop-title"
            className="max-h-[90dvh] w-full max-w-2xl overflow-auto rounded-md border border-red-200 bg-white shadow-xl"
          >
            <div className="flex items-start justify-between gap-3 border-b border-red-100 bg-red-50 px-4 py-3">
              <div>
                <h2 id="profile-oracle-drop-title" className="text-base font-semibold text-red-950">
                  {t("profiles.oracle.dropTitle")}
                </h2>
                <p className="mt-1 text-sm text-red-800">{t("profiles.oracle.dropHint")}</p>
              </div>
              <Button type="button" variant="ghost" size="sm" onClick={onCloseDrop}>
                <X size={15} aria-hidden="true" />
                <span>{t("tableMgmt.dropDialog.close")}</span>
              </Button>
            </div>
            <div className="grid gap-4 p-4">
              <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2">
                <p className="text-xs font-semibold text-red-800">{t("profiles.oracle.dropTarget")}</p>
                <p className="mt-1 break-all font-mono text-sm font-semibold text-red-950">{dropTargetName}</p>
              </div>
              <ExecutionConfirmationField
                value={dropConfirmation}
                onChange={onDropConfirmationChange}
                confirmed={dropConfirmed}
                placeholder={dropTargetName}
                expectedLabel={dropTargetName}
                helper={t("profiles.oracle.dropHint")}
                tone="danger"
              />
              <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
                <Button type="button" variant="danger" size="sm" loading={loading === "oracle-drop"} disabled={!dropConfirmed} onClick={onDropExecute}>
                  <Trash2 size={15} aria-hidden="true" />
                  <span>{t("profiles.oracle.drop")}</span>
                </Button>
                <Button type="button" variant="secondary" size="sm" onClick={onCloseDrop}>
                  <span>{t("tableMgmt.dropDialog.cancel")}</span>
                </Button>
              </div>
            </div>
          </section>
        </div>
      )}
    </>
  );
}

export function ProfileManagementPage() {
  const confirm = useConfirm();
  const [profiles, setProfiles] = useState<Nl2SqlProfile[]>([]);
  const [catalog, setCatalog] = useState<SchemaCatalog | null>(null);
  const [views, setViews] = useState<DbAdminObjectsData | null>(null);
  const [dbProfiles, setDbProfiles] = useState<SelectAiDbProfilesData | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedOracleProfileName, setSelectedOracleProfileName] = useState("");
  const [profileJson, setProfileJson] = useState("");
  const [form, setForm] = useState<ProfileFormState>(EMPTY_FORM);
  const [activeView, setActiveView] = useState<ActiveView>("list");
  const [profileStatusFilter, setProfileStatusFilter] = useState<ProfileStatusFilter>("active");
  const [profileSearch, setProfileSearch] = useState("");
  const [objectFilter, setObjectFilter] = useState("");
  const [profileSort, setProfileSort] = useState<SortState>({ key: "name", direction: "asc" });
  const [oraclePreview, setOraclePreview] = useState<SelectAiDbProfileMutationData | null>(null);
  const [oracleConfirmation, setOracleConfirmation] = useState("");
  const [dropTargetName, setDropTargetName] = useState("");
  const [dropConfirmation, setDropConfirmation] = useState("");
  const [loading, setLoading] = useState("");
  const [message, setMessage] = useState("");

  const selectedProfile = useMemo(
    () => profiles.find((profile) => profile.id === selectedId) ?? null,
    [profiles, selectedId]
  );
  const selectedOracleProfile = useMemo(
    () => (dbProfiles?.profiles ?? []).find((profile) => profile.name === selectedOracleProfileName) ?? null,
    [dbProfiles, selectedOracleProfileName]
  );

  const tableNames = useMemo(
    () =>
      (catalog?.tables ?? [])
        .filter((table) => !table.table_name.includes("$") && !table.table_type.toLowerCase().includes("view"))
        .map((table) => table.table_name)
        .sort(),
    [catalog]
  );
  const viewNames = useMemo(() => {
    const fromCatalog = (catalog?.tables ?? [])
      .filter((table) => !table.table_name.includes("$") && table.table_type.toLowerCase().includes("view"))
      .map((table) => table.table_name);
    const fromAdmin = (views?.items ?? []).filter((view) => !view.name.includes("$")).map((view) => view.name);
    return Array.from(new Set([...fromCatalog, ...fromAdmin])).sort();
  }, [catalog, views]);
  const filteredTableNames = useMemo(() => {
    const q = objectFilter.trim().toLowerCase();
    return q ? tableNames.filter((name) => name.toLowerCase().includes(q)) : tableNames;
  }, [objectFilter, tableNames]);
  const filteredViewNames = useMemo(() => {
    const q = objectFilter.trim().toLowerCase();
    return q ? viewNames.filter((name) => name.toLowerCase().includes(q)) : viewNames;
  }, [objectFilter, viewNames]);
  const filteredProfiles = useMemo(() => {
    const q = profileSearch.trim().toLowerCase();
    return profiles
      .filter((profile) => {
        if (profile.archived !== (profileStatusFilter === "archived")) return false;
        if (!q) return true;
        return (
          profile.name.toLowerCase().includes(q) ||
          (profile.category ?? "").toLowerCase().includes(q) ||
          profile.description.toLowerCase().includes(q)
        );
      })
      .sort((left, right) => {
        const a = profileSortValue(left, profileSort.key);
        const b = profileSortValue(right, profileSort.key);
        const result = a < b ? -1 : a > b ? 1 : 0;
        return profileSort.direction === "asc" ? result : -result;
      });
  }, [profileSearch, profileSort, profileStatusFilter, profiles]);

  const selectProfile = (profile: Nl2SqlProfile) => {
    const normalized = normalizeProfile(profile);
    setSelectedId(normalized.id);
    setForm(profileToForm(normalized));
    setOraclePreview(null);
    setOracleConfirmation("");
    setMessage("");
  };

  const selectOracleProfile = (profile: SelectAiDbProfile) => {
    setSelectedOracleProfileName(profile.name);
    setProfileJson(JSON.stringify(profile.attributes ?? {}, null, 2));
    setMessage("");
  };

  const applyDbProfiles = (data: SelectAiDbProfilesData, preferredName = selectedOracleProfileName) => {
    setDbProfiles(data);
    const nextOracle = preferredName
      ? data.profiles.find((profile) => profile.name === preferredName) ?? null
      : data.profiles[0] ?? null;
    if (nextOracle) {
      selectOracleProfile(nextOracle);
    } else {
      setSelectedOracleProfileName("");
      setProfileJson("");
    }
  };

  const load = async () => {
    setLoading("load");
    setMessage("");
    try {
      const [profileData, catalogData, viewData, dbProfileData] = await Promise.all([
        apiGet<Nl2SqlProfile[]>("/api/nl2sql/profiles?include_archived=true"),
        apiGet<SchemaCatalog>("/api/schema/catalog"),
        apiGet<DbAdminObjectsData>("/api/nl2sql/db-admin/views"),
        apiGet<SelectAiDbProfilesData>(BUSINESS_SELECT_AI_DB_PROFILES_DETAIL_URL),
      ]);
      const normalizedProfiles = profileData.map(normalizeProfile);
      setProfiles(normalizedProfiles);
      setCatalog(catalogData);
      setViews(viewData);
      applyDbProfiles(dbProfileData);
      const showingArchived = profileStatusFilter === "archived";
      const nextProfile =
        normalizedProfiles.find(
          (profile) => profile.id === selectedId && profile.archived === showingArchived
        ) ?? normalizedProfiles.find((profile) => profile.archived === showingArchived) ?? null;
      if (nextProfile) {
        setSelectedId(nextProfile.id);
        setForm(profileToForm(nextProfile));
      } else {
        setSelectedId(null);
        setForm(emptyProfileForm());
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("profiles.error.load"));
    } finally {
      setLoading("");
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const startNew = () => {
    setSelectedId(null);
    setForm(emptyProfileForm());
    setActiveView("create");
    setOraclePreview(null);
    setOracleConfirmation("");
    setMessage("");
  };

  const handleReset = () => {
    setForm(selectedProfile ? profileToForm(selectedProfile) : emptyProfileForm());
    setOraclePreview(null);
    setOracleConfirmation("");
    setMessage("");
  };

  const handleProfileStatusFilterChange = (status: ProfileStatusFilter) => {
    setProfileStatusFilter(status);
    setProfileSearch("");
    const nextProfile = profiles.find((profile) => profile.archived === (status === "archived")) ?? null;
    if (nextProfile) {
      selectProfile(nextProfile);
    } else {
      setSelectedId(null);
      setForm(emptyProfileForm());
      setOraclePreview(null);
      setOracleConfirmation("");
      setMessage("");
    }
  };

  const toggleObject = (kind: "table" | "view", name: string) => {
    setForm((current) => {
      const key = kind === "table" ? "allowedTables" : "allowedViews";
      const selected = current[key].includes(name);
      return {
        ...current,
        [key]: selected ? current[key].filter((item) => item !== name) : [...current[key], name],
      };
    });
  };

  const save = async () => {
    if (!form.name.trim()) {
      setMessage(t("profiles.error.nameRequired"));
      return;
    }
    setLoading("save");
    setMessage("");
    try {
      const payload = formToPayload(form);
      const saved = selectedId
        ? await apiPatch<Nl2SqlProfile>(`/api/nl2sql/profiles/${selectedId}`, payload)
        : await apiPost<Nl2SqlProfile>("/api/nl2sql/profiles", payload);
      await load();
      setSelectedId(saved.id);
      setForm(profileToForm(normalizeProfile(saved)));
      setActiveView("list");
      setMessage(t("profiles.message.saved"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("profiles.error.save"));
    } finally {
      setLoading("");
    }
  };

  const archiveSelected = async () => {
    if (!selectedProfile) return;
    const ok = await confirm({
      title: t("profiles.confirm.archiveTitle"),
      description: t("profiles.confirm.archiveDescription", { name: selectedProfile.name }),
      confirmLabel: t("profiles.action.archive"),
      tone: "danger",
      dismissOnOverlay: false,
    });
    if (!ok) return;
    setLoading("archive");
    try {
      await apiPost<Nl2SqlProfile>(`/api/nl2sql/profiles/${selectedProfile.id}/archive`);
      setSelectedId(null);
      setForm(emptyProfileForm());
      await load();
      toast.success(t("profiles.message.archived"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("profiles.error.archive"));
    } finally {
      setLoading("");
    }
  };

  const restoreSelected = async () => {
    if (!selectedProfile?.archived) return;
    setLoading("restore");
    try {
      const restored = await apiPost<Nl2SqlProfile>(`/api/nl2sql/profiles/${selectedProfile.id}/restore`);
      await load();
      setProfileStatusFilter("active");
      setSelectedId(restored.id);
      setForm(profileToForm(normalizeProfile(restored)));
      setActiveView("list");
      toast.success(t("profiles.message.restored"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("profiles.error.restore"));
    } finally {
      setLoading("");
    }
  };

  const executeOracleProfile = async () => {
    const profile = selectedProfile;
    if (!profile) return;
    setLoading("oracle-preview");
    setMessage("");
    try {
      const result = await apiPost<SelectAiDbProfileMutationData>(`/api/nl2sql/profiles/${profile.id}/select-ai-profile`, {
        confirmation: oracleConfirmation,
        reason: "ui-profile-management-select-ai-upsert",
      });
      setOraclePreview(result);
      applyDbProfiles(await apiGet<SelectAiDbProfilesData>(BUSINESS_SELECT_AI_DB_PROFILES_DETAIL_URL));
      setMessage(result.executed ? t("profiles.message.oracleSaved") : result.warnings.join(" ") || t("profiles.error.oracle"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("profiles.error.oracle"));
    } finally {
      setLoading("");
    }
  };

  const saveOracleJson = async () => {
    const profile = selectedOracleProfile;
    if (!profile) return;
    const ok = await confirm({
      title: t("profiles.oracle.saveConfirmTitle"),
      description: t("profiles.oracle.saveConfirmDescription", { name: profile.name }),
      confirmLabel: t("profiles.oracle.saveExecute"),
      tone: "danger",
      dismissOnOverlay: false,
    });
    if (!ok) return;
    setLoading("oracle-save");
    setMessage("");
    try {
      const attributes = JSON.parse(profileJson || "{}") as Record<string, unknown>;
      const result = await apiPost<SelectAiDbProfileMutationData>("/api/nl2sql/select-ai/db-profiles", {
        profile_name: profile.name,
        attributes,
        description: profile.description ?? "",
        category: profile.category ?? "",
        original_name: profile.name,
        confirmation: profile.name,
        reason: "ui-profile-management-oracle-json-save",
      });
      setOraclePreview(result);
      applyDbProfiles(await apiGet<SelectAiDbProfilesData>(BUSINESS_SELECT_AI_DB_PROFILES_DETAIL_URL));
      setMessage(result.executed ? t("profiles.message.oracleSaved") : result.warnings.join(" ") || t("profiles.error.oracle"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("profiles.error.oracle"));
    } finally {
      setLoading("");
    }
  };

  const dropOracleProfile = async () => {
    if (!dropTargetName) return;
    setLoading("oracle-drop");
    setMessage("");
    try {
      const result = await apiPost(`/api/nl2sql/select-ai/db-profiles/${encodeURIComponent(dropTargetName)}/drop`, {
        confirmation: dropConfirmation,
        reason: "ui-profile-management-oracle-drop",
      });
      void result;
      setDropTargetName("");
      setDropConfirmation("");
      applyDbProfiles(await apiGet<SelectAiDbProfilesData>(BUSINESS_SELECT_AI_DB_PROFILES_DETAIL_URL));
      setMessage(t("profiles.message.oracleDropped"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("profiles.error.oracle"));
    } finally {
      setLoading("");
    }
  };

  const toggleSort = (key: SortKey) => {
    setProfileSort((current) => ({
      key,
      direction: current.key === key && current.direction === "asc" ? "desc" : "asc",
    }));
  };

  const handleTabChange = (view: ActiveView) => {
    if (view === "create") {
      startNew();
      return;
    }
    setActiveView(view);
  };

  const editor = (
    <ProfileEditor
      selectedProfile={selectedProfile}
      form={form}
      tableNames={filteredTableNames}
      viewNames={filteredViewNames}
      objectFilter={objectFilter}
      saving={loading === "save" || loading === "archive" || loading === "restore"}
      oraclePreview={oraclePreview}
      oracleConfirmation={oracleConfirmation}
      oracleLoading={loading === "oracle-preview"}
      onObjectFilterChange={setObjectFilter}
      onFormChange={setForm}
      onToggleTable={(name) => toggleObject("table", name)}
      onToggleView={(name) => toggleObject("view", name)}
      onSave={() => void save()}
      onArchive={() => void archiveSelected()}
      onRestore={() => void restoreSelected()}
      onReset={handleReset}
      onOracleExecute={() => void executeOracleProfile()}
      onOracleConfirmationChange={setOracleConfirmation}
    />
  );

  return (
    <>
      <PageHeader
        title={t("nav.profiles")}
        subtitle={t("profiles.subtitle")}
      />

      <main className="grid gap-4 p-4 lg:p-8">
        {message && (
          <div className="flex flex-col gap-3 rounded-md border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700 sm:flex-row sm:items-center sm:justify-between" role="status">
            <span>{message}</span>
          </div>
        )}

        <StatusBar
          profileCount={profiles.filter((profile) => !profile.archived).length}
          objectCount={profiles
            .filter((profile) => !profile.archived)
            .reduce((sum, profile) => sum + profile.allowed_tables.length + (profile.allowed_views ?? []).length, 0)}
          oracleProfileCount={dbProfiles?.profiles.length ?? 0}
          runtime={dbProfiles?.runtime ?? "deterministic"}
          loading={loading === "load"}
          onRefresh={() => void load()}
        />

        <DbObjectManagementTabs
          activeView={activeView}
          tabs={[
            { id: "list", label: t("profiles.tabs.list"), icon: UserCog },
            { id: "create", label: t("profiles.tabs.create"), icon: Plus },
            { id: "oracle", label: t("profiles.tabs.oracle"), icon: Database },
          ] satisfies Array<DbObjectTab<ActiveView>>}
          idPrefix={PROFILE_MANAGEMENT_ID}
          ariaLabel={t("profiles.tabs.label")}
          onViewChange={handleTabChange}
        />

        {activeView === "list" ? (
          <DbObjectManagementPanelShell
            id="profile-management-panel-list"
            labelledBy="profile-management-tab-list"
            idPrefix={PROFILE_MANAGEMENT_ID}
            ariaLabel={t("profiles.workspace.label")}
            splitId="profile-management-list"
            preferredWidePane="right"
          >
            <ProfileList
              profiles={filteredProfiles}
              selectedId={selectedId}
              loading={loading === "load" && profiles.length === 0}
              search={profileSearch}
              statusFilter={profileStatusFilter}
              sort={profileSort}
              onSearchChange={setProfileSearch}
              onStatusFilterChange={handleProfileStatusFilterChange}
              onSortChange={toggleSort}
              onSelect={selectProfile}
            />
            {editor}
          </DbObjectManagementPanelShell>
        ) : activeView === "create" ? (
          <DbObjectManagementPanelShell
            id="profile-management-panel-create"
            labelledBy="profile-management-tab-create"
            idPrefix={PROFILE_MANAGEMENT_ID}
            ariaLabel={t("profiles.tabs.create")}
          >
            {editor}
          </DbObjectManagementPanelShell>
        ) : (
          <DbObjectManagementPanelShell
            id="profile-management-panel-oracle"
            labelledBy="profile-management-tab-oracle"
            idPrefix={PROFILE_MANAGEMENT_ID}
            ariaLabel={t("profiles.tabs.oracle")}
          >
            <OracleProfilesPanel
              dbProfiles={dbProfiles}
              selectedProfileName={selectedOracleProfileName}
              selectedProfile={selectedOracleProfile}
              profileJson={profileJson}
              dropTargetName={dropTargetName}
              dropConfirmation={dropConfirmation}
              loading={loading}
              onSelect={selectOracleProfile}
              onJsonChange={setProfileJson}
              onSaveJson={() => void saveOracleJson()}
              onOpenDrop={(name) => {
                setDropTargetName(name);
                setDropConfirmation("");
              }}
              onCloseDrop={() => setDropTargetName("")}
              onDropConfirmationChange={setDropConfirmation}
              onDropExecute={() => void dropOracleProfile()}
            />
          </DbObjectManagementPanelShell>
        )}
      </main>
    </>
  );
}
