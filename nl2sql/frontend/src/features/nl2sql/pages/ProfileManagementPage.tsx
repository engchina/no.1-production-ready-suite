import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  ArrowDownUp,
  ArrowLeft,
  Bot,
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
  toast,
} from "@engchina/production-ready-ui";

import { PageNotice } from "@/components/page-notice";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { apiDelete, apiGet, apiPatch, apiPost } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import { useSchemaOwners } from "@/lib/queries";
import {
  ExecutionConfirmationField,
} from "../components/DbAdminShared";
import {
  DbManagementSearchField,
  DbObjectManagementStatusBar,
  DbObjectPanelHeader,
} from "../components/DbObjectManagementShared";
import { engineLabel } from "../labels";
import {
  nl2sqlIncrementalKeys,
  getSchemaObjectSnapshot,
  useProfileDetail,
  useProfileSummaries,
  useSchemaCatalogHead,
  useSchemaObjects,
  useSchemaRefreshJob,
  useStartSchemaRefresh,
} from "../incrementalQueries";
import { BUSINESS_SELECT_AI_DB_PROFILES_URL } from "../selectAiProfileUrls";
import { schemaTableQualifiedName } from "../workbenchState";
import type {
  AssetRefreshData,
  Nl2SqlProfile,
  ProfileSummary,
  ProfileSelectAiConfig,
  ProfileSyncJobData,
  ProfileUpsertPayload,
  SchemaObjectSummary,
  SchemaTable,
  SelectAiDbProfileMutationData,
  SelectAiDbProfilesData,
} from "../types";

type ActiveView = "list" | "editor";
type SortKey = "name" | "tables" | "views";
type SortDirection = "asc" | "desc";

const panelClass = "grid gap-4 rounded-md border border-border bg-card p-4 shadow-sm";

function filterProfileObjects(objects: SchemaTable[], query: string) {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return objects;
  return objects.filter((object) =>
    [
      object.owner,
      object.table_name,
      schemaTableQualifiedName(object),
      object.logical_name,
      object.comment,
    ].some((value) => value.toLowerCase().includes(normalized))
  );
}

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
  region: "us-chicago-1",
  model: "xai.grok-4.3",
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

function profileSortValue(profile: ProfileSummary, key: SortKey) {
  if (key === "tables") return profile.allowed_table_count;
  if (key === "views") return profile.allowed_view_count;
  return profile.name.toLowerCase();
}

function schemaSummaryToTable(object: SchemaObjectSummary): SchemaTable {
  return {
    table_name: object.object_name,
    qualified_name: `${object.owner}.${object.object_name}`,
    logical_name: object.logical_name,
    owner: object.owner,
    table_type: object.object_type,
    comment: object.comment,
    row_count: object.row_count,
    columns: [],
    constraints: [],
  };
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
  schemaRefreshStatus,
  onSchemaRefresh,
}: {
  profileCount: number;
  objectCount: number;
  oracleProfileCount: number;
  runtime: string;
  loading: boolean;
  onRefresh: () => void;
  schemaRefreshStatus: string;
  onSchemaRefresh: () => void;
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
        <div className="flex flex-wrap items-center justify-end gap-2">
          <span className="sr-only" aria-live="polite" aria-atomic="true">
            {schemaRefreshStatus}
          </span>
          {schemaRefreshStatus && (
            <StatusBadge
              variant={
                schemaRefreshStatus === "done"
                  ? "success"
                  : schemaRefreshStatus === "error"
                    ? "danger"
                    : "info"
              }
              label={t(`profiles.schemaRefresh.status.${schemaRefreshStatus}`)}
            />
          )}
          <Button
            type="button"
            variant="secondary"
            size="sm"
            loading={schemaRefreshStatus === "pending" || schemaRefreshStatus === "running"}
            onClick={onSchemaRefresh}
          >
            <RefreshCw size={15} aria-hidden="true" />
            <span>{t("profiles.schemaRefresh.action")}</span>
          </Button>
          <Button type="button" variant="secondary" size="sm" loading={loading} onClick={onRefresh}>
            <RefreshCw size={15} aria-hidden="true" />
            <span>{t("profiles.action.refresh")}</span>
          </Button>
        </div>
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
  hasNextPage,
  loadingNextPage,
  onLoadMore,
}: {
  profiles: ProfileSummary[];
  loading: boolean;
  search: string;
  sort: SortState;
  onSearchChange: (value: string) => void;
  onSortChange: (key: SortKey) => void;
  onSelect: (profile: ProfileSummary) => void;
  onDelete: (profile: ProfileSummary) => void;
  onCreateNew: () => void;
  deletingId: string;
  hasNextPage: boolean;
  loadingNextPage: boolean;
  onLoadMore: () => void;
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
                      <td className="px-3 py-2 font-mono text-xs text-foreground">{profile.allowed_table_count}</td>
                      <td className="px-3 py-2 font-mono text-xs text-foreground">{profile.allowed_view_count}</td>
                      <td className="px-3 py-2 text-right">
                        <div className="flex flex-wrap justify-end gap-2">
                          <Button type="button" variant="secondary" size="sm" onClick={() => onSelect(profile)}>
                            <span>{t("profiles.action.select")}</span>
                          </Button>
                          {profile.id !== "default" ? (
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
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {hasNextPage && (
            <div className="flex justify-center border-t border-border p-3">
              <Button
                type="button"
                variant="secondary"
                size="sm"
                loading={loadingNextPage}
                onClick={onLoadMore}
              >
                {t("profiles.action.loadMore")}
              </Button>
            </div>
          )}
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

function SchemaObjectOption({
  object,
  selected,
  onToggle,
  className = "",
}: {
  object: SchemaTable;
  selected: boolean;
  onToggle: (name: string) => void;
  className?: string;
}) {
  const qualified = schemaTableQualifiedName(object);
  return (
    <label
      className={`flex min-h-11 cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 hover:bg-primary/5 focus-within:ring-2 focus-within:ring-ring/40 ${className}`}
    >
      <input
        type="checkbox"
        checked={selected}
        onChange={() => onToggle(qualified)}
        aria-label={qualified}
        className="h-4 w-4 shrink-0 accent-[var(--primary)]"
      />
      <span className="min-w-0 flex-1">
        <span className="block truncate font-mono text-xs font-medium text-foreground">
          {qualified}
        </span>
        <span className="block truncate text-xs text-muted">
          {object.logical_name || object.comment || object.table_name}
        </span>
      </span>
    </label>
  );
}

function VirtualizedSchemaOptions({
  entries,
  selectedSet,
  onToggle,
}: {
  entries: SchemaTable[];
  selectedSet: Set<string>;
  onToggle: (name: string) => void;
}) {
  const rowHeight = 52;
  const viewportHeight = 320;
  const [scrollTop, setScrollTop] = useState(0);
  const start = Math.max(0, Math.floor(scrollTop / rowHeight) - 5);
  const visibleCount = Math.ceil(viewportHeight / rowHeight) + 10;
  const visible = entries.slice(start, start + visibleCount);

  return (
    <div
      className="relative overflow-y-auto"
      style={{ height: viewportHeight }}
      onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
      data-testid="schema-object-virtual-list"
    >
      <div style={{ height: entries.length * rowHeight }} aria-hidden="true" />
      <div
        className="absolute inset-x-0 top-0 grid p-1"
        style={{ transform: `translateY(${start * rowHeight}px)` }}
      >
        {visible.map((object) => {
          const qualified = schemaTableQualifiedName(object);
          return (
            <SchemaObjectOption
              key={qualified}
              object={object}
              selected={selectedSet.has(qualified)}
              onToggle={onToggle}
            />
          );
        })}
      </div>
    </div>
  );
}

function SchemaGroupedSelectionPanel({
  title,
  objects,
  selectedItems,
  dataTestId,
  emptyTitle,
  emptyHint,
  onToggle,
  loading,
  hasNextPage,
  loadingNextPage,
  onLoadMore,
  ownerTotals,
  onToggleSchema,
}: {
  title: string;
  objects: SchemaTable[];
  selectedItems: string[];
  dataTestId: string;
  emptyTitle: string;
  emptyHint: string;
  onToggle: (name: string) => void;
  loading: boolean;
  hasNextPage: boolean;
  loadingNextPage: boolean;
  onLoadMore: () => void;
  ownerTotals: Record<string, number>;
  onToggleSchema: (owner: string, select: boolean) => Promise<void>;
}) {
  const [schemaSelectionOwner, setSchemaSelectionOwner] = useState("");
  const selectedSet = useMemo(
    () => new Set(selectedItems.map((name) => name.replaceAll('"', "").toUpperCase())),
    [selectedItems]
  );
  const groups = useMemo(() => {
    const grouped = new Map<string, SchemaTable[]>();
    for (const object of objects) {
      const owner = object.owner.toUpperCase();
      grouped.set(owner, [...(grouped.get(owner) ?? []), object]);
    }
    return [...grouped.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([owner, entries]) => ({
        owner,
        entries: entries.sort((left, right) =>
          schemaTableQualifiedName(left).localeCompare(schemaTableQualifiedName(right))
        ),
      }));
  }, [objects]);

  return (
    <section
      className="grid h-[392px] min-w-0 grid-rows-[auto_minmax(0,1fr)_auto] gap-2 overflow-hidden rounded-md border border-border bg-card p-3"
      aria-label={title}
      data-testid={dataTestId}
    >
      <div className="flex min-h-8 items-center justify-between gap-2">
        <h4 className="text-sm font-semibold text-foreground">{title}</h4>
        <span className="text-xs text-muted">
          {t("profiles.objects.selected", { count: selectedItems.length })}
        </span>
      </div>
      {loading ? (
        <div className="grid gap-2" aria-label={t("profiles.objects.loading")}>
          {Array.from({ length: 5 }, (_, index) => (
            <div key={index} className="h-11 animate-pulse rounded-md bg-muted/30" />
          ))}
        </div>
      ) : groups.length === 0 ? (
        <EmptyState title={emptyTitle} hint={emptyHint} />
      ) : (
        <div
          className="grid min-h-0 content-start gap-2 overflow-y-auto pr-1"
          data-testid={`${dataTestId}-scroll-region`}
        >
          {groups.map(({ owner, entries }) => {
            const selectedCount = [...selectedSet].filter((name) =>
              name.startsWith(`${owner}.`)
            ).length;
            const total = ownerTotals[owner] ?? entries.length;
            const allSelected = total > 0 && selectedCount >= total;
            return (
              <section
                key={owner}
                className="rounded-md border border-border bg-background"
                aria-label={t("profiles.objects.schemaGroup", { owner })}
              >
                <div className="flex min-h-11 items-center gap-2 border-b border-border bg-muted/15 px-2.5">
                  <span className="rounded border border-border bg-card px-2 py-0.5 font-mono text-xs font-semibold text-foreground">
                    {owner}
                  </span>
                  <span className="text-xs text-muted">
                    {t("profiles.objects.schemaCount", {
                      selected: selectedCount,
                      total,
                    })}
                  </span>
                  <button
                    type="button"
                    role="checkbox"
                    aria-checked={allSelected}
                    aria-busy={schemaSelectionOwner === owner}
                    aria-label={t("profiles.objects.selectSchema", { owner })}
                    disabled={Boolean(schemaSelectionOwner)}
                    className="ml-auto min-h-11 rounded-md px-2 text-xs font-medium text-primary hover:bg-primary/5 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 disabled:cursor-wait disabled:opacity-60"
                    onClick={async () => {
                      setSchemaSelectionOwner(owner);
                      try {
                        await onToggleSchema(owner, !allSelected);
                      } finally {
                        setSchemaSelectionOwner("");
                      }
                    }}
                  >
                    {allSelected
                      ? t("profiles.objects.clearSchema")
                      : t("profiles.objects.selectSchemaAction")}
                  </button>
                </div>
                {entries.length > 50 ? (
                  <VirtualizedSchemaOptions
                    entries={entries}
                    selectedSet={selectedSet}
                    onToggle={onToggle}
                  />
                ) : (
                  <div className="grid p-1">
                    {entries.map((object) => {
                      const qualified = schemaTableQualifiedName(object);
                      return (
                        <SchemaObjectOption
                          key={qualified}
                          object={object}
                          selected={selectedSet.has(qualified)}
                          onToggle={onToggle}
                        />
                      );
                    })}
                  </div>
                )}
              </section>
            );
          })}
        </div>
      )}
      {hasNextPage && (
        <Button
          type="button"
          variant="secondary"
          size="sm"
          loading={loadingNextPage}
          onClick={onLoadMore}
        >
          {t("profiles.action.loadMore")}
        </Button>
      )}
    </section>
  );
}

function ProfileEditor({
  selectedProfile,
  form,
  tableObjects,
  viewObjects,
  tableOwnerTotals,
  viewOwnerTotals,
  tableObjectsLoading,
  viewObjectsLoading,
  tableHasNextPage,
  viewHasNextPage,
  tableLoadingNextPage,
  viewLoadingNextPage,
  objectFilter,
  saving,
  nameError,
  oraclePreview,
  oracleConfirmation,
  rebuildAgentAssets,
  assetRefreshResults,
  oracleSyncJob,
  oracleSyncSubmissionError,
  retryingOracleSync,
  deleting,
  onObjectFilterChange,
  onFormChange,
  onNameErrorClear,
  onToggleTable,
  onToggleView,
  onToggleTableSchema,
  onToggleViewSchema,
  onLoadMoreTables,
  onLoadMoreViews,
  onSave,
  onDelete,
  onOracleConfirmationChange,
  onRebuildAgentAssetsChange,
  onRetryOracleSync,
  onBack,
}: {
  selectedProfile: Nl2SqlProfile | null;
  form: ProfileFormState;
  tableObjects: SchemaTable[];
  viewObjects: SchemaTable[];
  tableOwnerTotals: Record<string, number>;
  viewOwnerTotals: Record<string, number>;
  tableObjectsLoading: boolean;
  viewObjectsLoading: boolean;
  tableHasNextPage: boolean;
  viewHasNextPage: boolean;
  tableLoadingNextPage: boolean;
  viewLoadingNextPage: boolean;
  objectFilter: string;
  saving: boolean;
  nameError: boolean;
  oraclePreview: SelectAiDbProfileMutationData | null;
  oracleConfirmation: string;
  rebuildAgentAssets: boolean;
  assetRefreshResults: AssetRefreshData[];
  oracleSyncJob: ProfileSyncJobData | null;
  oracleSyncSubmissionError: string;
  retryingOracleSync: boolean;
  deleting: boolean;
  onObjectFilterChange: (value: string) => void;
  onFormChange: (updater: (current: ProfileFormState) => ProfileFormState) => void;
  onNameErrorClear: () => void;
  onToggleTable: (name: string) => void;
  onToggleView: (name: string) => void;
  onToggleTableSchema: (owner: string, select: boolean) => Promise<void>;
  onToggleViewSchema: (owner: string, select: boolean) => Promise<void>;
  onLoadMoreTables: () => void;
  onLoadMoreViews: () => void;
  onSave: () => void;
  onDelete: () => void;
  onOracleConfirmationChange: (value: string) => void;
  onRebuildAgentAssetsChange: (value: boolean) => void;
  onRetryOracleSync: () => void;
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
          selectedProfile && selectedProfile.id !== "default" ? (
            <Button type="button" variant="danger" size="sm" loading={deleting} onClick={onDelete}>
              <Trash2 size={15} aria-hidden="true" />
              <span>{t("profiles.action.delete")}</span>
            </Button>
          ) : undefined
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
                if (nameError) onNameErrorClear();
              }}
              aria-invalid={nameError}
              aria-describedby={nameError ? "profile-name-error" : undefined}
              className={inputClass}
            />
            {nameError && (
              <p
                id="profile-name-error"
                role="alert"
                className="flex items-center gap-1.5 text-xs font-normal text-danger"
              >
                <AlertCircle size={14} aria-hidden="true" />
                <span>{t("profiles.error.nameRequired")}</span>
              </p>
            )}
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
          <SchemaGroupedSelectionPanel
            title={t("profiles.objects.tablesTitle")}
            objects={tableObjects}
            selectedItems={form.allowedTables}
            dataTestId="profile-allowed-table-list"
            emptyTitle={t("profiles.objects.emptyTables")}
            emptyHint={t("profiles.objects.emptyTablesHint")}
            onToggle={onToggleTable}
            loading={tableObjectsLoading}
            hasNextPage={tableHasNextPage}
            loadingNextPage={tableLoadingNextPage}
            onLoadMore={onLoadMoreTables}
            ownerTotals={tableOwnerTotals}
            onToggleSchema={onToggleTableSchema}
          />
          <SchemaGroupedSelectionPanel
            title={t("profiles.objects.viewsTitle")}
            objects={viewObjects}
            selectedItems={form.allowedViews}
            dataTestId="profile-allowed-view-list"
            emptyTitle={t("profiles.objects.emptyViews")}
            emptyHint={t("profiles.objects.emptyViewsHint")}
            onToggle={onToggleView}
            loading={viewObjectsLoading}
            hasNextPage={viewHasNextPage}
            loadingNextPage={viewLoadingNextPage}
            onLoadMore={onLoadMoreViews}
            ownerTotals={viewOwnerTotals}
            onToggleSchema={onToggleViewSchema}
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
        <div>
          <h3 className="text-sm font-semibold text-foreground">{t("profiles.editor.oraclePreview")}</h3>
          <p className="mt-1 text-sm text-muted">{t("profiles.oracle.hint")}</p>
        </div>
        <OracleSyncStatusPanel
          job={oracleSyncJob}
          submissionError={oracleSyncSubmissionError}
          retrying={retryingOracleSync}
          onRetry={onRetryOracleSync}
        />
        {oraclePreview ? (
          <OracleMutationResult result={oraclePreview} />
        ) : (
          <div className="rounded-md border border-dashed border-border bg-background p-4 text-sm text-muted">
            {t("profiles.oracle.previewEmpty")}
          </div>
        )}

        <section className="grid gap-3 border-t border-border pt-4" aria-labelledby="profile-engine-assets-heading">
          <div>
            <h4 id="profile-engine-assets-heading" className="text-sm font-semibold text-foreground">
              {t("profiles.oracle.assets.title")}
            </h4>
            <p className="mt-1 text-sm text-muted">{t("profiles.oracle.assets.hint")}</p>
          </div>
          <label className="flex min-h-11 items-center gap-2 rounded-md border border-border bg-card p-3 text-sm text-foreground">
            <input
              type="checkbox"
              checked={rebuildAgentAssets}
              onChange={(event) => onRebuildAgentAssetsChange(event.currentTarget.checked)}
              className="h-4 w-4 rounded border-border text-primary focus:ring-ring/40"
            />
            <span>{t("profiles.oracle.assets.refreshAgent")}</span>
          </label>
          {assetRefreshResults.length > 0 && (
            <div className="grid gap-3 md:grid-cols-2" aria-label={t("profiles.oracle.assets.lastRefresh")}>
              {assetRefreshResults.map((result) => (
                <AssetStatusPanel key={result.engine} result={result} />
              ))}
            </div>
          )}
        </section>
      </section>

      <ExecutionConfirmationField
        value={oracleConfirmation}
        onChange={onOracleConfirmationChange}
        confirmed={oracleConfirmed}
        placeholder="ADMIN_EXECUTE"
        expectedLabel="ADMIN_EXECUTE"
        helper={t("profiles.oracle.executeHint")}
        actions={
          <Button
            type="button"
            variant="primary"
            size="md"
            loading={saving}
            disabled={!oracleConfirmed || saving}
            onClick={onSave}
          >
            <Save size={15} aria-hidden="true" />
            <span>{t("profiles.action.save")}</span>
          </Button>
        }
      />
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
        <pre className="overflow-auto rounded-md border border-border bg-card p-3 font-mono text-sm leading-6 text-foreground">
          <code>{result.ddl.join("\n")}</code>
        </pre>
      )}
      {result.profile && (
        <pre className="max-h-72 overflow-auto rounded-md border border-border bg-code p-3 font-mono text-sm leading-6 text-code-fg">
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

function OracleSyncStatusPanel({
  job,
  submissionError,
  retrying,
  onRetry,
}: {
  job: ProfileSyncJobData | null;
  submissionError: string;
  retrying: boolean;
  onRetry: () => void;
}) {
  if (!job && !submissionError) return null;
  const failed = job?.status === "failed" || Boolean(submissionError);
  const statusVariant =
    job?.status === "succeeded"
      ? "success"
      : failed
        ? "danger"
        : job?.status === "cancelled"
          ? "warning"
          : "info";
  const message = submissionError || job?.error_message_ja || "";
  const failureMessage = message
    ? `${t("profiles.oracle.sync.failed")} ${message}`
    : t("profiles.oracle.sync.failed");
  return (
    <section
      className="grid gap-2 rounded-md border border-border bg-background p-3"
      aria-live="polite"
      aria-atomic="true"
      data-testid="profile-oracle-sync-status"
    >
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge
          variant={statusVariant}
          label={t(`profiles.oracle.sync.status.${job?.status ?? "failed"}`)}
        />
        {job ? (
          <span className="text-sm text-muted">
            {t(`profiles.oracle.sync.phase.${job.phase}`)}
          </span>
        ) : null}
      </div>
      {failed ? (
        <PageNotice
          notice={{
            tone: "danger",
            message: failureMessage,
          }}
          action={
            <Button
              type="button"
              variant="secondary"
              size="sm"
              loading={retrying}
              onClick={onRetry}
            >
              <RefreshCw size={15} aria-hidden="true" />
              <span>{t("profiles.oracle.sync.retry")}</span>
            </Button>
          }
        />
      ) : null}
    </section>
  );
}

export function ProfileManagementPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const confirm = useConfirm();
  const queryClient = useQueryClient();
  const [form, setForm] = useState<ProfileFormState>(EMPTY_FORM);
  const [profileSearch, setProfileSearch] = useState("");
  const [objectFilter, setObjectFilter] = useState("");
  const [refreshJobId, setRefreshJobId] = useState("");
  const [profileSort, setProfileSort] = useState<SortState>({ key: "name", direction: "asc" });
  const [oraclePreview, setOraclePreview] = useState<SelectAiDbProfileMutationData | null>(null);
  const [oracleConfirmation, setOracleConfirmation] = useState("");
  const [rebuildAgentAssets, setRebuildAgentAssets] = useState(false);
  const [assetRefreshResults, setAssetRefreshResults] = useState<AssetRefreshData[]>([]);
  const [oracleSyncJobId, setOracleSyncJobId] = useState("");
  const [oracleSyncSubmissionError, setOracleSyncSubmissionError] = useState("");
  const reportedOracleSyncJobId = useRef("");
  const [loading, setLoading] = useState("");
  // message は初回ロード失敗の常設 Banner 専用。保存/削除の成否は toast、名前検証は nameError で扱う。
  const [message, setMessage] = useState("");
  const [nameError, setNameError] = useState(false);

  // ?profile= が唯一の情報源: null=一覧 / "new"=新規 / <id>=編集
  const profileParam = searchParams.get("profile");
  const activeView: ActiveView = profileParam ? "editor" : "list";
  const selectedProfileId = profileParam && profileParam !== "new" ? profileParam : "";
  const profilesQuery = useProfileSummaries(profileSearch);
  const profileDetailQuery = useProfileDetail(selectedProfileId);
  const tableObjectsQuery = useSchemaObjects(objectFilter, "TABLE");
  const viewObjectsQuery = useSchemaObjects(objectFilter, "VIEW");
  const schemaOwnersQuery = useSchemaOwners();
  const schemaHeadQuery = useSchemaCatalogHead();
  const startSchemaRefresh = useStartSchemaRefresh();
  const schemaRefreshJobQuery = useSchemaRefreshJob(refreshJobId);
  const schemaRefreshStatus = schemaRefreshJobQuery.isError
    ? "error"
    : (schemaRefreshJobQuery.data?.status ?? "");
  const dbProfilesQuery = useQuery({
    queryKey: ["nl2sql", "select-ai", "business-profiles"],
    queryFn: () => apiGet<SelectAiDbProfilesData>(BUSINESS_SELECT_AI_DB_PROFILES_URL),
    staleTime: 5_000,
  });
  const oracleSyncJobQuery = useQuery({
    queryKey: ["nl2sql", "oracle-sync-job", oracleSyncJobId],
    queryFn: () => apiGet<ProfileSyncJobData>(`/api/nl2sql/oracle-sync-jobs/${oracleSyncJobId}`),
    enabled: Boolean(oracleSyncJobId),
    refetchInterval: (query) => {
      const status = (query.state.data as ProfileSyncJobData | undefined)?.status;
      return status && ["succeeded", "failed", "cancelled"].includes(status) ? false : 1_000;
    },
  });
  const profiles = useMemo(
    () => profilesQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [profilesQuery.data]
  );
  const profilesLoaded = !profilesQuery.isPending;
  const selectedProfile = profileDetailQuery.data?.profile ?? null;
  const dbProfiles = dbProfilesQuery.data ?? null;
  const oracleSyncJob = oracleSyncJobQuery.data ?? null;

  const tableObjects = useMemo(
    () =>
      (tableObjectsQuery.data?.pages.flatMap((page) => page.items) ?? [])
        .map(schemaSummaryToTable),
    [tableObjectsQuery.data]
  );
  const viewObjects = useMemo(
    () =>
      (viewObjectsQuery.data?.pages.flatMap((page) => page.items) ?? [])
        .map(schemaSummaryToTable),
    [viewObjectsQuery.data]
  );
  const filteredTableObjects = useMemo(
    () => filterProfileObjects(tableObjects, objectFilter),
    [objectFilter, tableObjects]
  );
  const filteredViewObjects = useMemo(
    () => filterProfileObjects(viewObjects, objectFilter),
    [objectFilter, viewObjects]
  );
  const tableOwnerTotals = useMemo(
    () =>
      Object.fromEntries(
        (schemaOwnersQuery.data?.owners ?? []).map((item) => [
          item.owner.toUpperCase(),
          item.table_count,
        ])
      ),
    [schemaOwnersQuery.data]
  );
  const viewOwnerTotals = useMemo(
    () =>
      Object.fromEntries(
        (schemaOwnersQuery.data?.owners ?? []).map((item) => [
          item.owner.toUpperCase(),
          item.view_count,
        ])
      ),
    [schemaOwnersQuery.data]
  );
  const filteredProfiles = useMemo(() => {
    return profiles
      .sort((left, right) => {
        const a = profileSortValue(left, profileSort.key);
        const b = profileSortValue(right, profileSort.key);
        const result = a < b ? -1 : a > b ? 1 : 0;
        return profileSort.direction === "asc" ? result : -result;
      });
  }, [profileSort, profiles]);

  const selectProfile = (profile: ProfileSummary) => {
    setMessage("");
    setOracleSyncJobId("");
    setOracleSyncSubmissionError("");
    reportedOracleSyncJobId.current = "";
    setSearchParams({ profile: profile.id });
  };

  const load = async () => {
    setLoading("load");
    setMessage("");
    const results = await Promise.allSettled([
      profilesQuery.refetch(),
      tableObjectsQuery.refetch(),
      viewObjectsQuery.refetch(),
      schemaHeadQuery.refetch(),
      schemaOwnersQuery.refetch(),
      dbProfilesQuery.refetch(),
    ]);
    if (results.every((result) => result.status === "rejected")) {
      setMessage(t("profiles.error.load"));
    }
    setLoading("");
  };

  const runSchemaRefresh = async () => {
    try {
      const job = await startSchemaRefresh.mutateAsync();
      setRefreshJobId(job.job_id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("profiles.schemaRefresh.error"));
    }
  };

  useEffect(() => {
    const job = schemaRefreshJobQuery.data;
    if (!job) return;
    if (job.status === "done") {
      toast.success(
        t("profiles.schemaRefresh.done", {
          changed: job.changed_objects,
          version: job.catalog_version,
        })
      );
    } else if (job.status === "error") {
      toast.error(t("profiles.schemaRefresh.error"));
    }
  }, [schemaRefreshJobQuery.data?.status]);

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

  useEffect(() => {
    const job = oracleSyncJobQuery.data;
    if (!job || !["succeeded", "failed", "cancelled"].includes(job.status)) return;
    if (reportedOracleSyncJobId.current === job.job_id) return;
    reportedOracleSyncJobId.current = job.job_id;
    if (job.status === "succeeded") {
      if (job.oracle_result) setOraclePreview(job.oracle_result);
      if (job.agent_result) {
        setAssetRefreshResults((current) => [
          job.agent_result as AssetRefreshData,
          ...current.filter((item) => item.engine !== job.agent_result?.engine),
        ]);
      }
      void queryClient.invalidateQueries({
        queryKey: ["nl2sql", "select-ai", "business-profiles"],
      });
      toast.success(t("profiles.oracle.sync.succeeded"));
    } else if (job.status === "failed") {
      toast.error(t("profiles.oracle.sync.failed"));
    }
  }, [oracleSyncJobQuery.data, queryClient]);

  // 無効な id(削除済み等)の deep link は一覧へ縮退
  useEffect(() => {
    if (selectedProfileId && profileDetailQuery.isError) {
      setSearchParams({}, { replace: true });
    }
  }, [profileDetailQuery.isError, selectedProfileId, setSearchParams]);

  useEffect(() => {
    const error =
      profilesQuery.error ??
      tableObjectsQuery.error ??
      viewObjectsQuery.error ??
      schemaHeadQuery.error;
    setMessage(error instanceof Error ? error.message : "");
  }, [
    profilesQuery.error,
    schemaHeadQuery.error,
    tableObjectsQuery.error,
    viewObjectsQuery.error,
  ]);

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
    setOracleSyncJobId("");
    setOracleSyncSubmissionError("");
    reportedOracleSyncJobId.current = "";
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

  const toggleSchemaSnapshot = async (
    kind: "table" | "view",
    owner: string,
    select: boolean
  ) => {
    const key = kind === "table" ? "allowedTables" : "allowedViews";
    const ownerPrefix = `${owner.toUpperCase()}.`;
    try {
      const snapshot = select
        ? await getSchemaObjectSnapshot(owner, kind === "table" ? "TABLE" : "VIEW")
        : [];
      setForm((current) => {
        const retained = current[key].filter(
          (name) => !name.replaceAll('"', "").toUpperCase().startsWith(ownerPrefix)
        );
        return {
          ...current,
          [key]: select ? [...retained, ...snapshot] : retained,
        };
      });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("profiles.error.load"));
    }
  };

  const save = async () => {
    if (!form.name.trim()) {
      setNameError(true);
      return;
    }
    setNameError(false);
    setLoading("save");
    let saved: Nl2SqlProfile;
    try {
      const payload = formToPayload(form);
      saved = selectedProfile
        ? await apiPatch<Nl2SqlProfile>(
            `/api/nl2sql/profiles/${selectedProfile.id}`,
            payload,
            { "If-Match": `"${profileDetailQuery.data?.etag || selectedProfile.etag || ""}"` }
          )
        : await apiPost<Nl2SqlProfile>("/api/nl2sql/profiles", payload);
      queryClient.setQueryData(nl2sqlIncrementalKeys.profile(saved.id), {
        profile: saved,
        etag: saved.etag ?? "",
      });
      void queryClient.invalidateQueries({ queryKey: ["nl2sql", "profiles", "search"] });
      setForm(profileToForm(normalizeProfile(saved)));
      if (!selectedProfile) {
        setSearchParams({ profile: saved.id }, { replace: true });
      }
      toast.success(t("profiles.message.saved"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("profiles.error.save"));
      setLoading("");
      return;
    }

    try {
      setOracleSyncSubmissionError("");
      setOraclePreview(null);
      const job = await apiPost<ProfileSyncJobData>(
        `/api/nl2sql/profiles/${saved.id}/oracle-sync-jobs`,
        {
          confirmation: oracleConfirmation,
          reason: "ui-profile-management-save",
          rebuild_agent_assets: rebuildAgentAssets,
        },
        {
          headers: {
            "Idempotency-Key": `profile-save-${saved.id}-${saved.etag || "new"}`,
          },
        }
      );
      reportedOracleSyncJobId.current = "";
      setOracleSyncJobId(job.job_id);
      queryClient.setQueryData(["nl2sql", "oracle-sync-job", job.job_id], job);
    } catch (err) {
      setOracleSyncSubmissionError(
        err instanceof Error ? err.message : t("profiles.oracle.sync.failed")
      );
      toast.error(t("profiles.oracle.sync.savedButFailed"));
    } finally {
      setLoading("");
    }
  };

  const retryOracleSync = async () => {
    const profileId = oracleSyncJob?.profile_id || selectedProfile?.id;
    if (!profileId) return;
    setLoading("retry-oracle-sync");
    try {
      const job = oracleSyncJob?.status === "failed"
        ? await apiPost<ProfileSyncJobData>(
            `/api/nl2sql/oracle-sync-jobs/${oracleSyncJob.job_id}/retry`
          )
        : await apiPost<ProfileSyncJobData>(
            `/api/nl2sql/profiles/${profileId}/oracle-sync-jobs`,
            {
              confirmation: oracleConfirmation,
              reason: "ui-profile-management-retry",
              rebuild_agent_assets: rebuildAgentAssets,
            },
            { headers: { "Idempotency-Key": `profile-retry-${profileId}-${Date.now()}` } }
          );
      setOracleSyncSubmissionError("");
      reportedOracleSyncJobId.current = "";
      setOracleSyncJobId(job.job_id);
      queryClient.setQueryData(["nl2sql", "oracle-sync-job", job.job_id], job);
    } catch (err) {
      setOracleSyncSubmissionError(
        err instanceof Error ? err.message : t("profiles.oracle.sync.failed")
      );
    } finally {
      setLoading("");
    }
  };

  const deleteProfile = async (profile: Pick<Nl2SqlProfile, "id" | "name" | "etag">) => {
    if (profile.id === "default") return;
    const ok = await confirm({
      title: t("profiles.delete.confirm.title"),
      description: t("profiles.delete.confirm.description", { name: profile.name }),
      confirmLabel: t("common.delete"),
      tone: "danger",
      dismissOnOverlay: false,
    });
    if (!ok) return;

    setLoading(`delete-profile-${profile.id}`);
    try {
      const deleted = await apiDelete<Nl2SqlProfile>(
        `/api/nl2sql/profiles/${encodeURIComponent(profile.id)}`,
        { "If-Match": `"${profile.etag || profileDetailQuery.data?.etag || ""}"` }
      );
      queryClient.removeQueries({ queryKey: nl2sqlIncrementalKeys.profile(deleted.id) });
      await queryClient.invalidateQueries({ queryKey: ["nl2sql", "profiles", "search"] });
      if (profileParam) {
        setSearchParams({}, { replace: true });
      }
      toast.success(t("profiles.message.deleted", { name: deleted.name }));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("profiles.error.delete"));
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
      tableObjects={filteredTableObjects}
      viewObjects={filteredViewObjects}
      tableOwnerTotals={tableOwnerTotals}
      viewOwnerTotals={viewOwnerTotals}
      tableObjectsLoading={tableObjectsQuery.isPending}
      viewObjectsLoading={viewObjectsQuery.isPending}
      tableHasNextPage={Boolean(tableObjectsQuery.hasNextPage)}
      viewHasNextPage={Boolean(viewObjectsQuery.hasNextPage)}
      tableLoadingNextPage={tableObjectsQuery.isFetchingNextPage}
      viewLoadingNextPage={viewObjectsQuery.isFetchingNextPage}
      objectFilter={objectFilter}
      saving={loading === "save"}
      nameError={nameError}
      oraclePreview={oraclePreview}
      oracleConfirmation={oracleConfirmation}
      rebuildAgentAssets={rebuildAgentAssets}
      assetRefreshResults={assetRefreshResults}
      oracleSyncJob={oracleSyncJob}
      oracleSyncSubmissionError={oracleSyncSubmissionError}
      retryingOracleSync={loading === "retry-oracle-sync"}
      deleting={selectedProfile ? loading === `delete-profile-${selectedProfile.id}` : false}
      onObjectFilterChange={setObjectFilter}
      onFormChange={setForm}
      onToggleTable={(name) => toggleObject("table", name)}
      onToggleView={(name) => toggleObject("view", name)}
      onToggleTableSchema={(owner, select) =>
        toggleSchemaSnapshot("table", owner, select)
      }
      onToggleViewSchema={(owner, select) =>
        toggleSchemaSnapshot("view", owner, select)
      }
      onLoadMoreTables={() => void tableObjectsQuery.fetchNextPage()}
      onLoadMoreViews={() => void viewObjectsQuery.fetchNextPage()}
      onNameErrorClear={() => setNameError(false)}
      onSave={() => void save()}
      onDelete={() => {
        if (selectedProfile) void deleteProfile(selectedProfile);
      }}
      onOracleConfirmationChange={setOracleConfirmation}
      onRebuildAgentAssetsChange={setRebuildAgentAssets}
      onRetryOracleSync={() => void retryOracleSync()}
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
        <PageNotice
          notice={message ? { tone: "danger", message: `${message} ${t("profiles.error.retryHint")}` } : null}
          action={
            <Button type="button" variant="secondary" size="sm" onClick={() => void load()}>
              <RefreshCw size={15} aria-hidden="true" />
              <span>{t("profiles.action.refresh")}</span>
            </Button>
          }
        />

        {activeView === "list" ? (
          <>
            <StatusBar
              profileCount={profilesQuery.data?.pages[0]?.total ?? profiles.length}
              objectCount={schemaHeadQuery.data?.object_count ?? 0}
              oracleProfileCount={dbProfiles?.profiles.length ?? 0}
              runtime={dbProfiles?.runtime ?? "deterministic"}
              loading={loading === "load" || profilesQuery.isFetching}
              onRefresh={() => void load()}
              schemaRefreshStatus={schemaRefreshStatus}
              onSchemaRefresh={() => void runSchemaRefresh()}
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
                hasNextPage={Boolean(profilesQuery.hasNextPage)}
                loadingNextPage={profilesQuery.isFetchingNextPage}
                onLoadMore={() => void profilesQuery.fetchNextPage()}
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
              <div
                className="grid gap-2"
                data-testid="profile-editor-skeleton"
                aria-label={t("profiles.detail.loading")}
              >
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
