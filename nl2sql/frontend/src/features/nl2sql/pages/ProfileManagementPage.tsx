import { useEffect, useMemo, useState } from "react";
import {
  ArrowDownUp,
  ArrowLeft,
  Bot,
  Database,
  FileJson,
  Plus,
  RefreshCw,
  Save,
  Trash2,
  UserCog,
} from "lucide-react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";

import {
  Button,
  EmptyState,
  PageHeader,
  StatusBadge,
} from "@engchina/production-ready-ui";

import { useConfirm } from "@/components/ui/confirm-dialog";
import { apiDelete, apiGet, apiPatch, apiPost } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import {
  ExecutionConfirmationField,
  SelectionListPanel,
} from "../components/DbAdminShared";
import {
  DbManagementSearchField,
  DbObjectManagementStatusBar,
  DbObjectPanelHeader,
} from "../components/DbObjectManagementShared";
import { engineLabel } from "../labels";
import { BUSINESS_SELECT_AI_DB_PROFILES_DETAIL_URL } from "../selectAiProfileUrls";
import type {
  AssetRefreshData,
  DbAdminObjectsData,
  Nl2SqlProfile,
  ProfileSelectAiConfig,
  ProfileUpsertPayload,
  SchemaCatalog,
  SelectAiDbProfileMutationData,
  SelectAiDbProfilesData,
} from "../types";

type ActiveView = "list" | "editor";
type AssetRefreshKind = "select_ai" | "select_ai_agent";
type SortKey = "name" | "tables" | "views";
type SortDirection = "asc" | "desc";

const panelClass = "grid gap-4 rounded-md border border-border bg-card p-4 shadow-sm";

interface SortState {
  key: SortKey;
  direction: SortDirection;
}

interface ProfileFormState {
  name: string;
  category: string;
  allowedTables: string[];
  allowedViews: string[];
  glossaryText: string;
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
  allowedTables: [],
  allowedViews: [],
  glossaryText: "",
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
  "min-h-11 min-w-0 w-full rounded-md border border-border bg-card px-3 py-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-ring/40";
const textareaClass =
  "rounded-md border border-border bg-card px-3 py-2 text-sm leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40";

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

function mergeAdditionalInstructions(instructions: string, rules: string[]) {
  const base = instructions.trim();
  const seen = new Set(base.split("\n").map((line) => line.trim()).filter(Boolean));
  const additions = rules
    .map((rule) => rule.trim())
    .filter((rule) => {
      if (!rule || seen.has(rule)) return false;
      seen.add(rule);
      return true;
    });
  return [base, ...additions].filter(Boolean).join("\n");
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
  const selectAiConfig = {
    ...normalized.select_ai_config,
    additional_instructions: mergeAdditionalInstructions(
      normalized.select_ai_config.additional_instructions,
      normalized.sql_rules
    ),
  };
  return {
    name: normalized.name,
    category: normalized.category ?? "",
    allowedTables: normalized.allowed_tables,
    allowedViews: normalized.allowed_views,
    glossaryText: glossaryToText(normalized.glossary),
    fewShotText: fewShotToText(normalized.few_shot_examples),
    selectAiConfig,
  };
}

function formToPayload(form: ProfileFormState): ProfileUpsertPayload {
  return {
    name: form.name.trim(),
    category: form.category.trim(),
    allowed_tables: form.allowedTables,
    allowed_views: form.allowedViews,
    glossary: textToGlossary(form.glossaryText),
    sql_rules: [],
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
      className="inline-flex items-center gap-1 whitespace-nowrap text-left font-semibold text-foreground hover:text-foreground focus:outline-none focus:ring-2 focus:ring-ring/40"
      aria-sort={active ? (sort.direction === "asc" ? "ascending" : "descending") : "none"}
      onClick={() => onToggle(sortKey)}
    >
      <span>{label}</span>
      <ArrowDownUp size={13} className={active ? "text-primary" : "text-muted"} aria-hidden="true" />
    </button>
  );
}

function profileAttributesSql(profileName: string, description = "") {
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
  loading,
  search,
  sort,
  onSearchChange,
  onSortChange,
  onSelect,
  onDelete,
  onCreateNew,
  deletingId,
}: {
  profiles: Nl2SqlProfile[];
  loading: boolean;
  search: string;
  sort: SortState;
  onSearchChange: (value: string) => void;
  onSortChange: (key: SortKey) => void;
  onSelect: (profile: Nl2SqlProfile) => void;
  onDelete: (profile: Nl2SqlProfile) => void;
  onCreateNew: () => void;
  deletingId: string;
}) {
  return (
    <section className="grid min-w-0 content-start gap-3" aria-labelledby="profile-list-heading">
      <DbObjectPanelHeader
        headingId="profile-list-heading"
        icon={UserCog}
        title={t("profiles.list.title")}
        description={t("profiles.list.hint")}
        action={
          <div className="flex flex-wrap items-center justify-end gap-2">
            <StatusBadge variant="info" label={t("profiles.objects.count", { count: profiles.length })} />
            <Button type="button" variant="primary" size="sm" onClick={onCreateNew}>
              <Plus size={15} aria-hidden="true" />
              <span>{t("profiles.action.new")}</span>
            </Button>
          </div>
        }
      />
      <div className="grid gap-2 rounded-md border border-border bg-background p-3">
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
            <div key={index} className="h-12 animate-pulse rounded-md bg-muted/30" />
          ))}
        </div>
      ) : profiles.length === 0 ? (
        <EmptyState
          title={search.trim() ? t("profiles.list.noResultsTitle") : t("profiles.empty.title")}
          hint={search.trim() ? t("profiles.list.noResultsHint") : t("profiles.empty.hint")}
        />
      ) : (
        <div className="overflow-hidden rounded-md border border-border bg-card">
          <div className="max-h-[42rem] overflow-auto" data-testid="profile-management-list">
            <table className="w-full min-w-[38rem] table-fixed divide-y divide-border text-left text-sm" data-testid="profile-management-grid">
              <colgroup>
                <col />
                <col className="w-[5rem]" />
                <col className="w-[5rem]" />
                <col className="w-[12rem]" />
              </colgroup>
              <thead className="sticky top-0 z-10 bg-background text-xs text-muted">
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
              <tbody className="divide-y divide-border/70">
                {profiles.map((profile) => {
                  return (
                    <tr key={profile.id} className="hover:bg-background">
                      <td className="px-3 py-2 align-top">
                        <button
                          type="button"
                          className="grid max-w-full text-left focus:outline-none focus:ring-2 focus:ring-ring/40"
                          onClick={() => onSelect(profile)}
                        >
                          <span className="break-words font-semibold text-primary">{profile.name}</span>
                          <span className="line-clamp-2 text-xs leading-5 text-muted">{profile.category || "-"}</span>
                        </button>
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-foreground">{profile.allowed_tables.length}</td>
                      <td className="px-3 py-2 font-mono text-xs text-foreground">{(profile.allowed_views ?? []).length}</td>
                      <td className="px-3 py-2 text-right">
                        <div className="flex flex-wrap justify-end gap-2">
                          <Button type="button" variant="secondary" size="sm" onClick={() => onSelect(profile)}>
                            <span>{t("profiles.action.select")}</span>
                          </Button>
                          <Button
                            type="button"
                            variant="danger"
                            size="sm"
                            loading={deletingId === profile.id}
                            onClick={() => onDelete(profile)}
                          >
                            <Trash2 size={15} aria-hidden="true" />
                            <span>{t("profiles.action.delete")}</span>
                          </Button>
                        </div>
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
    <section className="grid gap-3 rounded-md border border-border bg-background p-3" aria-label={t("profiles.editor.selectAi")}>
      <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_10rem_10rem]">
        <label className="grid min-w-0 gap-1 text-sm font-medium text-foreground">
          <span>{t("profiles.field.profileName")}</span>
          <input
            value={form.selectAiConfig.profile_name}
            placeholder={t("profiles.placeholder.profileName")}
            onChange={(event) => updateSelectAiConfig(setForm, { profile_name: event.currentTarget.value })}
            className={inputClass}
          />
        </label>
        <label className="grid min-w-0 gap-1 text-sm font-medium text-foreground">
          <span>{t("profiles.field.region")}</span>
          <input
            value={form.selectAiConfig.region}
            placeholder="ap-osaka-1"
            onChange={(event) => updateSelectAiConfig(setForm, { region: event.currentTarget.value })}
            className={inputClass}
          />
        </label>
        <label className="grid min-w-0 gap-1 text-sm font-medium text-foreground">
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
        <label className="grid gap-1 text-sm font-medium text-foreground">
          <span>{t("profiles.field.model")}</span>
          <input
            value={form.selectAiConfig.model}
            onChange={(event) => updateSelectAiConfig(setForm, { model: event.currentTarget.value })}
            className={inputClass}
          />
        </label>
        <label className="grid gap-1 text-sm font-medium text-foreground">
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
          <label key={key} className="flex min-h-11 items-center gap-2 rounded-md border border-border bg-card p-3 text-sm text-foreground">
            <input
              type="checkbox"
              checked={Boolean(form.selectAiConfig[key])}
              onChange={(event) => updateSelectAiConfig(setForm, { [key]: event.currentTarget.checked })}
              className="h-4 w-4 rounded border-border text-primary focus:ring-ring/40"
            />
            <span>{t(labelKey)}</span>
          </label>
        ))}
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <div className="grid content-start gap-1 text-sm font-medium text-foreground">
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
          <p id="profile-select-ai-role-hint" className="text-xs font-normal leading-5 text-muted">
            {t("profiles.field.roleHint")}
          </p>
        </div>
        <div className="grid content-start gap-1 text-sm font-medium text-foreground">
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
            className="text-xs font-normal leading-5 text-muted"
          >
            {t("profiles.field.additionalInstructionsHint")}
          </p>
        </div>
      </div>
    </section>
  );
}

function defaultOracleProfileName(profile: Nl2SqlProfile | null) {
  return profile ? `NL2SQL_${profile.id.toUpperCase()}_PROFILE` : "";
}

function CreateProfileSqlPreview({
  selectedProfile,
  form,
}: {
  selectedProfile: Nl2SqlProfile | null;
  form: ProfileFormState;
}) {
  const configuredName = form.selectAiConfig.profile_name.trim();
  const profileName = configuredName || defaultOracleProfileName(selectedProfile);
  const sql = profileName ? profileAttributesSql(profileName) : "";

  return (
    <section
      className="grid gap-3 rounded-md border border-border bg-background p-3"
      aria-labelledby="profile-create-sql-preview-heading"
      data-testid="profile-create-profile-sql-preview"
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h3 id="profile-create-sql-preview-heading" className="text-sm font-semibold text-foreground">
            {t("profiles.oracle.sql")}
          </h3>
          <p className="mt-1 text-sm leading-6 text-muted">{t("profiles.oracle.sqlHint")}</p>
        </div>
        <StatusBadge
          variant={profileName ? "neutral" : "info"}
          label={profileName || t("profiles.oracle.sqlPendingBadge")}
        />
      </div>
      {sql ? (
        <pre className="max-w-full overflow-auto rounded-md border border-border bg-card p-3 font-mono text-xs leading-5 text-foreground">
          <code>{sql}</code>
        </pre>
      ) : (
        <div className="rounded-md border border-dashed border-border bg-card p-4 text-sm leading-6 text-muted">
          {t("profiles.oracle.sqlPending")}
        </div>
      )}
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
  assetRefreshResults,
  assetRefreshLoading,
  deleting,
  onObjectFilterChange,
  onFormChange,
  onToggleTable,
  onToggleView,
  onSave,
  onDelete,
  onOracleExecute,
  onOracleConfirmationChange,
  onAssetRefresh,
  onBack,
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
  assetRefreshResults: AssetRefreshData[];
  assetRefreshLoading: string;
  deleting: boolean;
  onObjectFilterChange: (value: string) => void;
  onFormChange: (updater: (current: ProfileFormState) => ProfileFormState) => void;
  onToggleTable: (name: string) => void;
  onToggleView: (name: string) => void;
  onSave: () => void;
  onDelete: () => void;
  onOracleExecute: () => void;
  onOracleConfirmationChange: (value: string) => void;
  onAssetRefresh: (kind: AssetRefreshKind) => void;
  onBack: () => void;
}) {
  const oracleConfirmed = oracleConfirmation.trim() === "ADMIN_EXECUTE";
  return (
    <section className="grid min-w-0 content-start gap-4" aria-labelledby="profile-editor-heading">
      <div className="justify-self-start">
        <Button type="button" variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft size={15} aria-hidden="true" />
          <span>{t("profiles.action.backToList")}</span>
        </Button>
      </div>
      <DbObjectPanelHeader
        headingId="profile-editor-heading"
        icon={FileJson}
        title={
          selectedProfile
            ? t("profiles.editor.editNamed", { name: selectedProfile.name })
            : t("profiles.editor.new")
        }
        description={t("profiles.editor.hint")}
        action={
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button type="button" variant="primary" size="sm" loading={saving} onClick={onSave}>
              <Save size={15} aria-hidden="true" />
              <span>{t("profiles.action.save")}</span>
            </Button>
            {selectedProfile && (
              <>
                <span className="hidden h-6 border-l border-border sm:block" aria-hidden="true" />
                <Button type="button" variant="danger" size="sm" loading={deleting} onClick={onDelete}>
                  <Trash2 size={15} aria-hidden="true" />
                  <span>{t("profiles.action.delete")}</span>
                </Button>
              </>
            )}
          </div>
        }
      />

      <section className="grid gap-3 rounded-md border border-border bg-background p-3">
        <h3 className="text-sm font-semibold text-foreground">{t("profiles.editor.basic")}</h3>
        <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_14rem]">
          <label className="grid gap-1 text-sm font-medium text-foreground">
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
          <label className="grid gap-1 text-sm font-medium text-foreground">
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
        </div>
      </section>

      <section data-testid="profile-allowed-object-list" className="grid gap-3">
        <div>
          <h3 className="text-sm font-semibold text-foreground">{t("profiles.editor.objects")}</h3>
          <p className="mt-1 text-sm text-muted">{t("profiles.field.allowedObjects")}</p>
        </div>
        <div
          className="grid gap-2 rounded-md border border-border bg-background p-3"
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

      <section
        id="profile-learning"
        className="grid scroll-mt-4 gap-3 rounded-md border border-border bg-background p-3 focus:outline-none focus:ring-2 focus:ring-ring/40"
        tabIndex={-1}
      >
        <h3 className="text-sm font-semibold text-foreground">{t("profiles.editor.learning")}</h3>
        <div className="grid gap-3 lg:grid-cols-2">
          <label className="grid gap-1 text-sm font-medium text-foreground">
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
          <label className="grid gap-1 text-sm font-medium text-foreground">
            <span>{t("profiles.field.fewShot")}</span>
            <textarea
              value={form.fewShotText}
              rows={6}
              placeholder={t("profiles.placeholder.fewShot")}
              onChange={(event) => {
                const value = event.currentTarget.value;
                onFormChange((current) => ({ ...current, fewShotText: value }));
              }}
              className={`${textareaClass} font-mono`}
            />
          </label>
        </div>
      </section>

      <SelectAiConfigFields form={form} setForm={onFormChange} />

      <section className="grid gap-3 rounded-md border border-border bg-card p-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h3 className="text-sm font-semibold text-foreground">{t("profiles.editor.oraclePreview")}</h3>
            <p className="mt-1 text-sm text-muted">{t("profiles.oracle.hint")}</p>
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
          <div className="rounded-md border border-dashed border-border bg-background p-4 text-sm text-muted">
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

        <section className="grid gap-3 border-t border-border pt-4" aria-labelledby="profile-engine-assets-heading">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <h4 id="profile-engine-assets-heading" className="text-sm font-semibold text-foreground">
                {t("profiles.oracle.assets.title")}
              </h4>
              <p className="mt-1 text-sm text-muted">{t("profiles.oracle.assets.hint")}</p>
            </div>
            <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:justify-end">
              <Button
                type="button"
                variant="secondary"
                size="sm"
                loading={assetRefreshLoading === "asset-refresh-select_ai"}
                disabled={!selectedProfile}
                onClick={() => onAssetRefresh("select_ai")}
              >
                <RefreshCw size={15} aria-hidden="true" />
                <span>{t("profiles.oracle.assets.refreshSelectAi")}</span>
              </Button>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                loading={assetRefreshLoading === "asset-refresh-select_ai_agent"}
                disabled={!selectedProfile}
                onClick={() => onAssetRefresh("select_ai_agent")}
              >
                <RefreshCw size={15} aria-hidden="true" />
                <span>{t("profiles.oracle.assets.refreshAgent")}</span>
              </Button>
            </div>
          </div>
          {assetRefreshResults.length > 0 && (
            <div className="grid gap-3 md:grid-cols-2" aria-label={t("profiles.oracle.assets.lastRefresh")}>
              {assetRefreshResults.map((result) => (
                <AssetStatusPanel key={result.engine} result={result} />
              ))}
            </div>
          )}
        </section>
      </section>

      <CreateProfileSqlPreview selectedProfile={selectedProfile} form={form} />
    </section>
  );
}

function OracleMutationResult({ result }: { result: SelectAiDbProfileMutationData }) {
  return (
    <section className="grid gap-2 rounded-md border border-border bg-background p-3 text-sm" data-testid="profile-oracle-result">
      <div className="flex flex-wrap gap-2">
        <StatusBadge variant={result.executed ? "success" : result.status === "error" ? "danger" : "neutral"} label={result.status} />
        <StatusBadge variant="neutral" label={result.runtime} />
        {result.profile_name && <StatusBadge variant="info" label={result.profile_name} />}
      </div>
      {result.warnings.map((warning) => (
        <p key={warning} className="rounded-md border border-warning/30 bg-warning-bg px-3 py-2 text-warning">
          {warning}
        </p>
      ))}
      {result.ddl.length > 0 && (
        <pre className="overflow-auto rounded-md border border-border bg-card p-3 font-mono text-xs leading-5 text-foreground">
          <code>{result.ddl.join("\n")}</code>
        </pre>
      )}
      {result.profile && (
        <pre className="max-h-72 overflow-auto rounded-md border border-border bg-code p-3 font-mono text-xs leading-5 text-code-fg">
          <code>{JSON.stringify(result.profile.attributes ?? {}, null, 2)}</code>
        </pre>
      )}
    </section>
  );
}

function AssetStatusPanel({ result }: { result: AssetRefreshData }) {
  return (
    <section className="grid gap-3 rounded-md border border-border bg-background p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2">
          <Bot size={17} className="mt-0.5 shrink-0 text-primary" aria-hidden="true" />
          <div className="min-w-0">
            <p className="font-semibold text-foreground">{engineLabel(result.engine)}</p>
            <p className="mt-1 text-xs text-muted">
              {result.refreshed_at ? new Date(result.refreshed_at).toLocaleString("ja-JP") : "-"}
            </p>
          </div>
        </div>
        <StatusBadge variant={result.refreshed ? "success" : "warning"} label={result.status} />
      </div>
      <dl className="grid gap-2 text-sm">
        {Object.entries(result.asset_names).map(([name, value]) => (
          <div key={name} className="grid min-w-0 gap-1 rounded-md bg-card p-2">
            <dt className="text-xs font-medium uppercase text-muted">{name}</dt>
            <dd className="break-all font-mono text-xs text-foreground">{value}</dd>
          </div>
        ))}
      </dl>
      {result.warning && (
        <p className="rounded-md border border-warning/30 bg-warning-bg px-3 py-2 text-sm text-warning">
          {result.warning}
        </p>
      )}
    </section>
  );
}

export function ProfileManagementPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const confirm = useConfirm();
  const [profiles, setProfiles] = useState<Nl2SqlProfile[]>([]);
  const [profilesLoaded, setProfilesLoaded] = useState(false);
  const [catalog, setCatalog] = useState<SchemaCatalog | null>(null);
  const [tables, setTables] = useState<DbAdminObjectsData | null>(null);
  const [views, setViews] = useState<DbAdminObjectsData | null>(null);
  const [dbProfiles, setDbProfiles] = useState<SelectAiDbProfilesData | null>(null);
  const [form, setForm] = useState<ProfileFormState>(EMPTY_FORM);
  const [profileSearch, setProfileSearch] = useState("");
  const [objectFilter, setObjectFilter] = useState("");
  const [profileSort, setProfileSort] = useState<SortState>({ key: "name", direction: "asc" });
  const [oraclePreview, setOraclePreview] = useState<SelectAiDbProfileMutationData | null>(null);
  const [oracleConfirmation, setOracleConfirmation] = useState("");
  const [assetRefreshResults, setAssetRefreshResults] = useState<AssetRefreshData[]>([]);
  const [loading, setLoading] = useState("");
  const [message, setMessage] = useState("");

  // ?profile= が唯一の情報源: null=一覧 / "new"=新規 / <id>=編集
  const profileParam = searchParams.get("profile");
  const activeView: ActiveView = profileParam ? "editor" : "list";
  const selectedProfile = useMemo(
    () =>
      profileParam && profileParam !== "new"
        ? profiles.find((profile) => profile.id === profileParam && !profile.archived) ?? null
        : null,
    [profiles, profileParam]
  );

  const tableNames = useMemo(() => {
    const fromCatalog = (catalog?.tables ?? [])
      .filter((table) => !table.table_name.includes("$") && !table.table_type.toLowerCase().includes("view"))
      .map((table) => table.table_name);
    const fromAdmin = (tables?.items ?? []).filter((table) => !table.name.includes("$")).map((table) => table.name);
    return Array.from(new Set([...fromCatalog, ...fromAdmin])).sort();
  }, [catalog, tables]);
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
        if (profile.archived) return false;
        if (!q) return true;
        return (
          profile.name.toLowerCase().includes(q) ||
          (profile.category ?? "").toLowerCase().includes(q)
        );
      })
      .sort((left, right) => {
        const a = profileSortValue(left, profileSort.key);
        const b = profileSortValue(right, profileSort.key);
        const result = a < b ? -1 : a > b ? 1 : 0;
        return profileSort.direction === "asc" ? result : -result;
      });
  }, [profileSearch, profileSort, profiles]);

  const selectProfile = (profile: Nl2SqlProfile) => {
    setMessage("");
    setSearchParams({ profile: profile.id });
  };

  const applyDbProfiles = (data: SelectAiDbProfilesData) => {
    setDbProfiles(data);
  };

  const load = async () => {
    setLoading("load");
    setMessage("");
    try {
      const [profileData, catalogData, tableData, viewData, dbProfileData] = await Promise.all([
        apiGet<Nl2SqlProfile[]>("/api/nl2sql/profiles"),
        apiGet<SchemaCatalog>("/api/schema/catalog"),
        apiGet<DbAdminObjectsData>("/api/nl2sql/db-admin/tables"),
        apiGet<DbAdminObjectsData>("/api/nl2sql/db-admin/views"),
        apiGet<SelectAiDbProfilesData>(BUSINESS_SELECT_AI_DB_PROFILES_DETAIL_URL),
      ]);
      const normalizedProfiles = profileData.map(normalizeProfile);
      setProfiles(normalizedProfiles);
      setProfilesLoaded(true);
      setCatalog(catalogData);
      setTables(tableData);
      setViews(viewData);
      applyDbProfiles(dbProfileData);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("profiles.error.load"));
    } finally {
      setLoading("");
    }
  };

  useEffect(() => {
    void load();
  }, []);

  // 編集対象の切替時にフォームと編集付帯 state を同期する(deep link 初回ロード後も含む)
  const editTargetKey = selectedProfile?.id ?? (profileParam === "new" ? "new" : "");
  useEffect(() => {
    if (!editTargetKey) return;
    setForm(selectedProfile ? profileToForm(selectedProfile) : emptyProfileForm());
    setOraclePreview(null);
    setOracleConfirmation("");
    setAssetRefreshResults([]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editTargetKey]);

  // 無効な id(削除済み等)の deep link は一覧へ縮退
  useEffect(() => {
    if (!profilesLoaded) return;
    if (profileParam && profileParam !== "new" && !selectedProfile) {
      setSearchParams({}, { replace: true });
    }
  }, [profilesLoaded, profileParam, selectedProfile, setSearchParams]);

  // legacy hash 導線: #profile-learning を ?profile= 付き URL へ正規化する
  useEffect(() => {
    if (location.hash !== "#profile-learning" || profileParam || !profilesLoaded) return;
    const target = profiles.find((profile) => !profile.archived)?.id ?? "new";
    navigate(
      { pathname: location.pathname, search: `?profile=${target}`, hash: location.hash },
      { replace: true }
    );
  }, [location.hash, location.pathname, navigate, profileParam, profiles, profilesLoaded]);

  useEffect(() => {
    if (location.hash !== "#profile-learning" || activeView !== "editor") return;
    const frame = window.requestAnimationFrame(() => {
      const target = document.getElementById("profile-learning");
      target?.scrollIntoView({ block: "start", inline: "nearest", behavior: "auto" });
      target?.focus({ preventScroll: true });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [activeView, location.hash, selectedProfile?.id]);

  const startNew = () => {
    setMessage("");
    setSearchParams({ profile: "new" });
  };

  // dirty 判定: 読み込み時と同じ変換を再計算して比較する(追加 state 不要)
  const isDirty = useMemo(() => {
    const baseline = selectedProfile ? profileToForm(selectedProfile) : emptyProfileForm();
    return JSON.stringify(form) !== JSON.stringify(baseline);
  }, [form, selectedProfile]);

  const backToList = async () => {
    if (isDirty) {
      const ok = await confirm({
        title: t("profiles.discard.confirm.title"),
        description: t("profiles.discard.confirm.description"),
        confirmLabel: t("profiles.discard.confirm.confirm"),
        tone: "danger",
        dismissOnOverlay: false,
      });
      if (!ok) return;
    }
    setSearchParams({});
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
      const saved = selectedProfile
        ? await apiPatch<Nl2SqlProfile>(`/api/nl2sql/profiles/${selectedProfile.id}`, payload)
        : await apiPost<Nl2SqlProfile>("/api/nl2sql/profiles", payload);
      await load();
      setForm(profileToForm(normalizeProfile(saved)));
      if (!selectedProfile) {
        setSearchParams({ profile: saved.id }, { replace: true });
      }
      setMessage(t("profiles.message.saved"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("profiles.error.save"));
    } finally {
      setLoading("");
    }
  };

  const deleteProfile = async (profile: Nl2SqlProfile) => {
    const ok = await confirm({
      title: t("profiles.delete.confirm.title"),
      description: t("profiles.delete.confirm.description", { name: profile.name }),
      confirmLabel: t("common.delete"),
      tone: "danger",
      dismissOnOverlay: false,
    });
    if (!ok) return;

    setLoading(`delete-profile-${profile.id}`);
    setMessage("");
    try {
      const deleted = await apiDelete<Nl2SqlProfile>(
        `/api/nl2sql/profiles/${encodeURIComponent(profile.id)}`
      );
      setProfiles((current) => current.filter((item) => item.id !== deleted.id));
      await load();
      if (profileParam) {
        setSearchParams({}, { replace: true });
      }
      setMessage(t("profiles.message.deleted", { name: deleted.name }));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("profiles.error.delete"));
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

  const refreshEngineAssets = async (kind: AssetRefreshKind) => {
    const profile = selectedProfile;
    if (!profile) return;
    const loadingKey = `asset-refresh-${kind}`;
    setLoading(loadingKey);
    setMessage("");
    try {
      const path =
        kind === "select_ai"
          ? "/api/nl2sql/select-ai/profiles/refresh"
          : "/api/nl2sql/select-ai-agent/assets/refresh";
      const result = await apiPost<AssetRefreshData>(
        `${path}?profile_id=${encodeURIComponent(profile.id)}`
      );
      setAssetRefreshResults((current) => [
        result,
        ...current.filter((item) => item.engine !== result.engine),
      ]);
      setMessage(t("profiles.message.assetRefreshed", { engine: engineLabel(result.engine) }));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("profiles.error.assetRefresh"));
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

  const editor = (
    <ProfileEditor
      selectedProfile={selectedProfile}
      form={form}
      tableNames={filteredTableNames}
      viewNames={filteredViewNames}
      objectFilter={objectFilter}
      saving={loading === "save"}
      oraclePreview={oraclePreview}
      oracleConfirmation={oracleConfirmation}
      oracleLoading={loading === "oracle-preview"}
      assetRefreshResults={assetRefreshResults}
      assetRefreshLoading={loading}
      deleting={selectedProfile ? loading === `delete-profile-${selectedProfile.id}` : false}
      onObjectFilterChange={setObjectFilter}
      onFormChange={setForm}
      onToggleTable={(name) => toggleObject("table", name)}
      onToggleView={(name) => toggleObject("view", name)}
      onSave={() => void save()}
      onDelete={() => {
        if (selectedProfile) void deleteProfile(selectedProfile);
      }}
      onOracleExecute={() => void executeOracleProfile()}
      onOracleConfirmationChange={setOracleConfirmation}
      onAssetRefresh={(kind) => void refreshEngineAssets(kind)}
      onBack={() => void backToList()}
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
          <div className="flex flex-col gap-3 rounded-md border border-border bg-background px-4 py-3 text-sm text-foreground sm:flex-row sm:items-center sm:justify-between" role="status">
            <span>{message}</span>
          </div>
        )}

        {activeView === "list" ? (
          <>
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
            <section
              id="profile-management-panel-list"
              aria-label={t("profiles.workspace.label")}
              className={panelClass}
            >
              <ProfileList
                profiles={filteredProfiles}
                loading={loading === "load" && profiles.length === 0}
                search={profileSearch}
                sort={profileSort}
                onSearchChange={setProfileSearch}
                onSortChange={toggleSort}
                onSelect={selectProfile}
                onDelete={(profile) => void deleteProfile(profile)}
                onCreateNew={startNew}
                deletingId={loading.startsWith("delete-profile-") ? loading.replace("delete-profile-", "") : ""}
              />
            </section>
          </>
        ) : (
          <section
            id="profile-management-panel-editor"
            aria-label={selectedProfile ? t("profiles.editor.edit") : t("profiles.editor.new")}
            className={panelClass}
          >
            {selectedProfile || profileParam === "new" ? (
              editor
            ) : (
              <div className="grid gap-2" data-testid="profile-editor-skeleton">
                {Array.from({ length: 6 }, (_, index) => (
                  <div key={index} className="h-12 animate-pulse rounded-md bg-muted/30" />
                ))}
              </div>
            )}
          </section>
        )}
      </main>
    </>
  );
}
