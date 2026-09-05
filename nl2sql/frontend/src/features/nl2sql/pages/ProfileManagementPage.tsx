import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDownUp,
  ArrowLeft,
  FileJson,
  Plus,
  RefreshCw,
  Save,
  Trash2,
  UserCog,
} from "lucide-react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { FieldError } from "@/components/ui/field-error";
import { Banner, EmptyState, toast } from "@engchina/production-ready-ui";

import { BulkSelectionActions } from "@/components/BulkSelectionActions";
import { isInteractiveRowTarget } from "@/components/MasterDetailDataTable";
import { ObjectActionBar } from "@/components/ObjectActions";
import { PageHeader, PageHeaderStatusBadge } from "@/components/PageHeader";
import { ProcessingIndicator } from "@/components/ProcessingState";
import { PageNotice } from "@/components/page-notice";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { FieldLabel } from "@/components/ui/required-field";
import { SelectField, type SelectFieldOption } from "@/components/ui/select-field";
import { StatusBadge } from "@/components/ui/status-badge";
import { ApiError, apiDelete, apiGet, apiPatch, apiPost, isTimeoutError } from "@/lib/api";
import { t } from "@/lib/i18n";
import { INFORMATION_TABLE_FOCUS_CLASS } from "@/lib/list-density";
import { toastError } from "@/lib/toast";
import { LIST_SEARCH_DEBOUNCE_MS, useDebouncedValue } from "@/lib/useDebouncedValue";
import { useSchemaOwners, useSelectAiCredential } from "@/lib/queries";
import { API_TIMEOUT_MS, requestTimeoutSeconds } from "@/lib/requestPolicy";
import { useAuth } from "@/features/security/AuthProvider";
import { MENU_PERMISSIONS } from "@/features/security/menu-permissions";
import { securityApi } from "@/features/security/api";
import type { ProfileAccessProfile } from "@/features/security/types";
import { ExecutionConfirmationField } from "../components/DbAdminShared";
import {
  DbManagementSearchField,
  DbObjectManagementPanelShell,
  DbObjectPanelHeader,
  DbObjectSelectorFooter,
  DbObjectSelectorToolbar,
} from "../components/DbObjectManagementShared";
import {
  SchemaRefreshHeaderStatus,
  SchemaRefreshProcessing,
} from "../components/SchemaRefreshFeedback";
import { ProfileSaveProgress } from "../components/ProfileSaveProgress";
import { useSchemaRefreshCoordinator } from "../SchemaRefreshCoordinator";
import {
  nl2sqlIncrementalKeys,
  getSchemaObjectSnapshot,
  useProfileDetail,
  useProfileSummaries,
  useSchemaCatalogHead,
  useSchemaObjects,
  useSelectAiDbProfileRefreshJob,
  useStartSelectAiDbProfileRefresh,
} from "../incrementalQueries";
import { isUserVisibleObjectName } from "../objectVisibility";
import {
  applySchemaBulkSelection,
  normalizeObjectKey,
  selectedObjectKeys,
  toggleObjectSelection,
} from "../profileObjectSelection";
import {
  SCHEMA_OPTION_ROW_HEIGHT,
  SCHEMA_OPTION_VIEWPORT_HEIGHT,
  schemaOptionWindow,
} from "../profileVirtualList";
import type { ProfileListSortKey, ProfileListSortState } from "../profileListState";
import { BUSINESS_SELECT_AI_DB_PROFILES_URL } from "../selectAiProfileUrls";
import { schemaTableQualifiedName } from "../workbenchState";
import type {
  Nl2SqlProfile,
  ProfileDeleteData,
  ProfileSummary,
  ProfileSelectAiConfig,
  ProfileSyncJobData,
  ProfileUpsertPayload,
  SchemaObjectSummary,
  SchemaTable,
  SelectAiDbProfileRefreshJobData,
  SelectAiDbProfilesData,
} from "../types";

type ActiveView = "list" | "editor";
type ProfileNameError = "required" | "format" | "duplicate" | null;
type ProfileRequiredField = "category" | "region" | "model" | "maxTokens" | "embeddingModel";
type ProfileRequiredErrors = Partial<Record<ProfileRequiredField, true>>;
type DbProfileRefreshSignal = {
  profile_list_refresh_job_id?: string | null;
  profile_list_refresh_required?: boolean | null;
  profile_list_refresh_reason_code?: string | null;
};

const SELECT_AI_MAX_TOKENS_MIN = 4096;
const SELECT_AI_MAX_TOKENS_MAX = 32000;
const SELECT_AI_DEFAULT_REGION = "ap-osaka-1";
const SELECT_AI_REGION_OPTIONS = [
  { value: "ap-osaka-1", label: "ap-osaka-1" },
  { value: "us-chicago-1", label: "us-chicago-1" },
] as const satisfies readonly SelectFieldOption<string>[];
const PROFILE_NAME_PATTERN = /^[A-Z][A-Z0-9_]*$/u;

interface ProfileFormState {
  name: string;
  category: string;
  allowedTables: string[];
  allowedViews: string[];
  selectAiConfig: ProfileSelectAiConfig;
}

const DEFAULT_SELECT_AI_CONFIG: ProfileSelectAiConfig = {
  profile_name: "",
  region: SELECT_AI_DEFAULT_REGION,
  model: "xai.grok-4.3",
  embedding_model: "cohere.embed-v4.0",
  max_tokens: SELECT_AI_MAX_TOKENS_MAX,
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
  selectAiConfig: DEFAULT_SELECT_AI_CONFIG,
};

function emptyProfileForm(region = SELECT_AI_DEFAULT_REGION): ProfileFormState {
  return {
    ...EMPTY_FORM,
    allowedTables: [],
    allowedViews: [],
    selectAiConfig: { ...DEFAULT_SELECT_AI_CONFIG, region: normalizeSelectAiRegion(region) },
  };
}

const inputClass =
  "min-h-11 min-w-0 w-full rounded-md border border-border bg-card px-3 py-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-ring/40";
const textareaClass =
  "rounded-md border border-border bg-card px-3 py-2 text-sm leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40";

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

function normalizeSelectAiMaxTokens(value: unknown) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return SELECT_AI_MAX_TOKENS_MAX;
  return Math.min(SELECT_AI_MAX_TOKENS_MAX, Math.max(SELECT_AI_MAX_TOKENS_MIN, Math.trunc(numeric)));
}

function normalizeSelectAiRegion(value: unknown) {
  const region = typeof value === "string" ? value.trim() : "";
  return SELECT_AI_REGION_OPTIONS.some((option) => option.value === region)
    ? region
    : SELECT_AI_DEFAULT_REGION;
}

function parseMaxTokensInput(value: string) {
  const numeric = Number(value);
  return value.trim() && Number.isFinite(numeric) ? numeric : SELECT_AI_MAX_TOKENS_MAX;
}

function normalizeProfileName(value: string) {
  return value.trim().toUpperCase();
}

function profileNameError(value: string): ProfileNameError {
  const normalized = normalizeProfileName(value);
  if (!normalized) return "required";
  return PROFILE_NAME_PATTERN.test(normalized) ? null : "format";
}

function profileNameErrorMessage(error: Exclude<ProfileNameError, null>) {
  if (error === "required") return t("profiles.error.nameRequired");
  if (error === "duplicate") return t("profiles.error.nameDuplicate");
  return t("profiles.error.nameFormat");
}

function isProfileNameConflictError(error: unknown) {
  if (!(error instanceof ApiError) || error.status !== 422) return false;
  if (error.errorCode === "NL2SQL_PROFILE_NAME_CONFLICT") return true;
  return error.fieldErrors.some(
    (fieldError) =>
      fieldError.pointer === "/name" && fieldError.code === "profile_name_conflict"
  );
}

function profileRequiredErrors(form: ProfileFormState): ProfileRequiredErrors {
  const errors: ProfileRequiredErrors = {};
  if (!form.category.trim()) errors.category = true;
  if (!form.selectAiConfig.region.trim()) errors.region = true;
  if (!form.selectAiConfig.model.trim()) errors.model = true;
  if (!Number.isFinite(Number(form.selectAiConfig.max_tokens))) errors.maxTokens = true;
  if (!form.selectAiConfig.embedding_model.trim()) errors.embeddingModel = true;
  return errors;
}

function hasProfileRequiredErrors(errors: ProfileRequiredErrors) {
  return Object.keys(errors).length > 0;
}

function schemaObjectQueryTotal(
  pages: Array<{ total: number | null }> | undefined,
  loadedCount: number
) {
  const total = pages?.[0]?.total;
  return typeof total === "number" ? Math.max(total, loadedCount) : loadedCount;
}

function listLoadMoreErrorMessage(error: unknown, fallbackKey: Parameters<typeof t>[0]) {
  if (isTimeoutError(error)) {
    return t("objectSelector.loadMoreTimeout", {
      seconds: requestTimeoutSeconds(API_TIMEOUT_MS.interactiveList),
    });
  }
  return error instanceof Error ? error.message : t(fallbackKey);
}

function normalizeProfile(profile: Nl2SqlProfile): Nl2SqlProfile {
  const selectAiConfig = { ...DEFAULT_SELECT_AI_CONFIG, ...(profile.select_ai_config ?? {}) };
  return {
    ...profile,
    allowed_tables: (profile.allowed_tables ?? []).filter(isUserVisibleObjectName),
    allowed_views: (profile.allowed_views ?? []).filter(isUserVisibleObjectName),
    select_ai_config: {
      ...selectAiConfig,
      region: normalizeSelectAiRegion(selectAiConfig.region),
      max_tokens: normalizeSelectAiMaxTokens(selectAiConfig.max_tokens),
    },
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
    selectAiConfig,
  };
}

function formToPayload(form: ProfileFormState): ProfileUpsertPayload {
  const profileName = normalizeProfileName(form.name);
  return {
    name: profileName,
    category: form.category.trim(),
    allowed_tables: form.allowedTables,
    allowed_views: form.allowedViews,
    sql_rules: [],
    safety_policy: "select_only",
    select_ai_config: {
      ...form.selectAiConfig,
      profile_name: profileName,
      region: normalizeSelectAiRegion(form.selectAiConfig.region),
      model: form.selectAiConfig.model.trim(),
      embedding_model: form.selectAiConfig.embedding_model.trim() || "cohere.embed-v4.0",
      max_tokens: normalizeSelectAiMaxTokens(form.selectAiConfig.max_tokens),
      role: form.selectAiConfig.role.trim(),
      additional_instructions: form.selectAiConfig.additional_instructions.trim(),
    },
  };
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
  sortKey: ProfileListSortKey;
  sort: ProfileListSortState;
  onToggle: (key: ProfileListSortKey) => void;
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

function ProfileList({
  profiles,
  totalCount,
  selectedProfileId,
  loading,
  search,
  sort,
  onSearchChange,
  onSortChange,
  onSelect,
  hasNextPage,
  loadingNextPage,
  loadMoreError,
  onLoadMore,
  onRetryLoadMore,
}: {
  profiles: ProfileSummary[];
  totalCount: number;
  selectedProfileId: string;
  loading: boolean;
  search: string;
  sort: ProfileListSortState;
  onSearchChange: (value: string) => void;
  onSortChange: (key: ProfileListSortKey) => void;
  onSelect: (profile: ProfileSummary) => void;
  hasNextPage: boolean;
  loadingNextPage: boolean;
  loadMoreError: string;
  onLoadMore: () => void;
  onRetryLoadMore: () => void;
}) {
  return (
    <section className="grid min-w-0 content-start gap-3" aria-labelledby="profile-list-heading">
      <DbObjectPanelHeader
        headingId="profile-list-heading"
        icon={UserCog}
        title={t("profiles.list.title")}
        description={t("profiles.list.hint")}
        action={
          <StatusBadge variant="info" label={t("profiles.objects.count", { count: totalCount })} />
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
          <div
            className={`max-h-[20rem] max-w-full overflow-x-hidden overflow-y-auto md:max-h-[30.5rem] ${INFORMATION_TABLE_FOCUS_CLASS}`}
            role="region"
            tabIndex={0}
            aria-label={t("profiles.list.scrollLabel")}
            data-testid="profile-management-list"
          >
            <table className="w-full max-w-[34rem] table-fixed divide-y divide-border text-left text-sm" data-testid="profile-management-grid">
              <colgroup>
                <col />
                <col className="w-[6.5rem]" />
                <col className="w-[6.5rem]" />
              </colgroup>
              <thead className="sticky top-0 z-10 bg-background text-xs text-muted">
                <tr>
                  <th className="px-3 py-2">
                    <SortButton label={t("profiles.field.name")} sortKey="name" sort={sort} onToggle={onSortChange} />
                  </th>
                  <th className="px-3 py-2 text-right">
                    <SortButton label={t("profiles.field.allowedTables")} sortKey="tables" sort={sort} onToggle={onSortChange} />
                  </th>
                  <th className="px-3 py-2 text-right">
                    <SortButton label={t("profiles.field.allowedViews")} sortKey="views" sort={sort} onToggle={onSortChange} />
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/70">
                {profiles.map((profile) => {
                  const selected = profile.id === selectedProfileId;
                  return (
                    <tr
                      key={profile.id}
                      data-selected={selected ? "true" : "false"}
                      aria-current={selected ? "true" : undefined}
                      className={[
                        "cursor-pointer transition-colors",
                        selected ? "bg-primary/10" : "hover:bg-background",
                      ].join(" ")}
                      onClick={(event) => {
                        if (isInteractiveRowTarget(event.target)) return;
                        onSelect(profile);
                      }}
                    >
                      <td className="px-3 py-2 align-top">
                        <button
                          type="button"
                          className="grid max-w-full text-left focus:outline-none focus:ring-2 focus:ring-ring/40"
                          aria-current={selected ? "true" : undefined}
                          aria-label={t("profiles.action.selectProfile", { name: profile.name })}
                          onClick={() => onSelect(profile)}
                        >
                          <span className="break-words font-semibold text-primary">{profile.name}</span>
                          <span className="line-clamp-2 text-xs leading-5 text-muted">{profile.category || "-"}</span>
                        </button>
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-xs text-foreground">{profile.allowed_table_count}</td>
                      <td className="px-3 py-2 text-right font-mono text-xs text-foreground">{profile.allowed_view_count}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {!loading && profiles.length > 0 && (hasNextPage || loadMoreError) && (
        <div className="grid justify-items-end gap-2" data-testid="profile-management-load-more">
          {loadMoreError ? (
            <Banner
              severity="danger"
              action={
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  className="w-full sm:w-auto"
                  loading={loadingNextPage}
                  onClick={onRetryLoadMore}
                >
                  <RefreshCw size={15} aria-hidden="true" />
                  <span>{t("common.retry")}</span>
                </Button>
              }
            >
              {loadMoreError}
            </Banner>
          ) : (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="w-full sm:w-auto"
              loading={loadingNextPage}
              onClick={onLoadMore}
            >
              {t("profiles.action.loadMore")}
            </Button>
          )}
        </div>
      )}
    </section>
  );
}

function SelectAiConfigFields({
  form,
  setForm,
  requiredErrors,
  onRequiredErrorClear,
}: {
  form: ProfileFormState;
  setForm: (updater: (current: ProfileFormState) => ProfileFormState) => void;
  requiredErrors: ProfileRequiredErrors;
  onRequiredErrorClear: (field: ProfileRequiredField) => void;
}) {
  return (
    <section
      id="profile-select-ai"
      className="grid scroll-mt-4 gap-3 rounded-md border border-border bg-background p-3 focus:outline-none focus:ring-2 focus:ring-ring/40"
      aria-label={t("profiles.editor.selectAi")}
      tabIndex={-1}
    >
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-[minmax(8rem,0.9fr)_minmax(12rem,1.2fr)_minmax(8rem,0.8fr)_minmax(12rem,1.2fr)]">
        <SelectField
          id="profile-select-ai-region"
          label={t("profiles.field.region")}
          value={form.selectAiConfig.region}
          options={SELECT_AI_REGION_OPTIONS}
          required
          error={requiredErrors.region ? t("profiles.error.regionRequired") : undefined}
          onValueChange={(value) => {
            updateSelectAiConfig(setForm, { region: value });
            if (requiredErrors.region) onRequiredErrorClear("region");
          }}
          className="min-w-0"
          buttonClassName="h-11"
        />
        <div className="grid min-w-0 content-start gap-1">
          <FieldLabel
            htmlFor="profile-select-ai-model"
            label={t("profiles.field.model")}
            required
          />
          <input
            id="profile-select-ai-model"
            value={form.selectAiConfig.model}
            required
            aria-required="true"
            aria-invalid={Boolean(requiredErrors.model)}
            aria-describedby={requiredErrors.model ? "profile-select-ai-model-error" : undefined}
            onChange={(event) => {
              updateSelectAiConfig(setForm, { model: event.currentTarget.value });
              if (requiredErrors.model) onRequiredErrorClear("model");
            }}
            className={`${inputClass} ${
              requiredErrors.model ? "border-danger focus:border-danger focus:ring-danger/40" : ""
            }`}
          />
          {requiredErrors.model && (
            <RequiredFieldError id="profile-select-ai-model-error">
              {t("profiles.error.modelRequired")}
            </RequiredFieldError>
          )}
        </div>
        <div className="grid min-w-0 content-start gap-1">
          <FieldLabel
            htmlFor="profile-select-ai-max-tokens"
            label={t("profiles.field.maxTokens")}
            required
          />
          <input
            id="profile-select-ai-max-tokens"
            type="number"
            min={SELECT_AI_MAX_TOKENS_MIN}
            max={SELECT_AI_MAX_TOKENS_MAX}
            step={1}
            value={form.selectAiConfig.max_tokens}
            required
            aria-required="true"
            aria-invalid={Boolean(requiredErrors.maxTokens)}
            aria-describedby={
              requiredErrors.maxTokens ? "profile-select-ai-max-tokens-error" : undefined
            }
            onChange={(event) => {
              updateSelectAiConfig(setForm, {
                max_tokens: parseMaxTokensInput(event.currentTarget.value),
              });
              if (requiredErrors.maxTokens) onRequiredErrorClear("maxTokens");
            }}
            onBlur={(event) =>
              updateSelectAiConfig(setForm, {
                max_tokens: normalizeSelectAiMaxTokens(event.currentTarget.value),
              })
            }
            className={`${inputClass} ${
              requiredErrors.maxTokens
                ? "border-danger focus:border-danger focus:ring-danger/40"
                : ""
            }`}
          />
          {requiredErrors.maxTokens && (
            <RequiredFieldError id="profile-select-ai-max-tokens-error">
              {t("profiles.error.maxTokensRequired")}
            </RequiredFieldError>
          )}
        </div>
        <div className="grid min-w-0 content-start gap-1">
          <FieldLabel
            htmlFor="profile-select-ai-embedding-model"
            label={t("profiles.field.embeddingModel")}
            required
          />
          <input
            id="profile-select-ai-embedding-model"
            value={form.selectAiConfig.embedding_model}
            required
            aria-required="true"
            aria-invalid={Boolean(requiredErrors.embeddingModel)}
            aria-describedby={
              requiredErrors.embeddingModel ? "profile-select-ai-embedding-model-error" : undefined
            }
            onChange={(event) => {
              updateSelectAiConfig(setForm, { embedding_model: event.currentTarget.value });
              if (requiredErrors.embeddingModel) onRequiredErrorClear("embeddingModel");
            }}
            className={`${inputClass} ${
              requiredErrors.embeddingModel
                ? "border-danger focus:border-danger focus:ring-danger/40"
                : ""
            }`}
          />
          {requiredErrors.embeddingModel && (
            <RequiredFieldError id="profile-select-ai-embedding-model-error">
              {t("profiles.error.embeddingModelRequired")}
            </RequiredFieldError>
          )}
        </div>
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

function RequiredFieldError({ id, children }: { id: string; children: string }) {
  return <FieldError id={id} message={children} />;
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
      className={`flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 hover:bg-primary/5 focus-within:ring-2 focus-within:ring-ring/40 ${className}`}
      style={{ height: SCHEMA_OPTION_ROW_HEIGHT }}
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
  const [scrollTop, setScrollTop] = useState(0);
  const { start, end, offset, totalHeight } = schemaOptionWindow(scrollTop, entries.length);
  const visible = entries.slice(start, end);

  return (
    <div
      className="relative overflow-y-auto px-1"
      style={{ height: SCHEMA_OPTION_VIEWPORT_HEIGHT }}
      onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
      data-testid="schema-object-virtual-list"
    >
      <div style={{ height: totalHeight }} aria-hidden="true" />
      {/* 行は固定高。padding を挟むと offset と実描画位置がずれるため付けない。 */}
      <div
        className="absolute inset-x-0 top-0 grid"
        style={{ transform: `translateY(${offset}px)` }}
      >
        {visible.map((object) => {
          const qualified = schemaTableQualifiedName(object);
          return (
            <SchemaObjectOption
              key={qualified}
              object={object}
              selected={selectedSet.has(normalizeObjectKey(qualified))}
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
  totalCount,
  selectedItems,
  dataTestId,
  emptyTitle,
  emptyHint,
  onToggle,
  loading,
  hasNextPage,
  loadingNextPage,
  loadMoreError,
  onLoadMore,
  onRetryLoadMore,
  ownerTotals,
  onToggleSchema,
}: {
  title: string;
  objects: SchemaTable[];
  totalCount: number;
  selectedItems: string[];
  dataTestId: string;
  emptyTitle: string;
  emptyHint: string;
  onToggle: (name: string) => void;
  loading: boolean;
  hasNextPage: boolean;
  loadingNextPage: boolean;
  loadMoreError: string;
  onLoadMore: () => void;
  onRetryLoadMore: () => void;
  ownerTotals: Record<string, number>;
  onToggleSchema: (owner: string, select: boolean) => Promise<void>;
}) {
  const [schemaSelectionOwner, setSchemaSelectionOwner] = useState("");
  const selectedSet = useMemo(() => selectedObjectKeys(selectedItems), [selectedItems]);
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
  const toggleSchemaSelection = async (owner: string, select: boolean) => {
    setSchemaSelectionOwner(owner);
    try {
      await onToggleSchema(owner, select);
    } finally {
      setSchemaSelectionOwner("");
    }
  };

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
            const ownerKeyPrefix = normalizeObjectKey(`${owner}.`);
            const selectedCount = [...selectedSet].filter((name) =>
              name.startsWith(ownerKeyPrefix)
            ).length;
            const total = ownerTotals[owner] ?? entries.length;
            const allSelected = total > 0 && selectedCount >= total;
            const noneSelected = selectedCount === 0;
            return (
              <section
                key={owner}
                className="rounded-md border border-border bg-background"
                aria-label={t("profiles.objects.schemaGroup", { owner })}
              >
                <div className="grid min-h-11 gap-2 border-b border-border bg-muted/15 px-2.5 py-1.5">
                  <div
                    className="flex min-w-0 flex-wrap items-center gap-2"
                    data-testid={`${dataTestId}-${owner.toLowerCase()}-schema-heading`}
                  >
                    <span className="rounded border border-border bg-card px-2 py-0.5 font-mono text-xs font-semibold text-foreground">
                      {owner}
                    </span>
                    <span className="text-xs text-muted">
                      {t("profiles.objects.schemaCount", {
                        selected: selectedCount,
                        total,
                      })}
                    </span>
                  </div>
                  <BulkSelectionActions
                    selectLabel={t("profiles.objects.selectSchemaAction")}
                    clearLabel={t("profiles.objects.clearSchema")}
                    selectAriaLabel={t("common.selection.selectGroup", { name: owner })}
                    clearAriaLabel={t("common.selection.clearGroup", { name: owner })}
                    selectDisabled={allSelected || Boolean(schemaSelectionOwner)}
                    clearDisabled={noneSelected || Boolean(schemaSelectionOwner)}
                    busy={schemaSelectionOwner === owner}
                    dataTestId={`${dataTestId}-${owner.toLowerCase()}-schema-bulk-actions`}
                    onSelectAll={() => void toggleSchemaSelection(owner, true)}
                    onClearAll={() => void toggleSchemaSelection(owner, false)}
                  />
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
                          selected={selectedSet.has(normalizeObjectKey(qualified))}
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
      <DbObjectSelectorFooter
        visibleCount={objects.length}
        totalCount={totalCount}
        selectedCount={selectedItems.length}
        hasNextPage={hasNextPage}
        loadingNextPage={loadingNextPage}
        loadMoreError={loadMoreError}
        loadMoreLabel={t("profiles.action.loadMore")}
        dataTestId={`${dataTestId}-footer`}
        onLoadMore={onLoadMore}
        onRetryLoadMore={onRetryLoadMore}
      />
    </section>
  );
}

function ProfileEditor({
  selectedProfile,
  profileAccessProfile,
  form,
  tableObjects,
  viewObjects,
  tableObjectTotal,
  viewObjectTotal,
  tableOwnerTotals,
  viewOwnerTotals,
  tableObjectsLoading,
  viewObjectsLoading,
  tableHasNextPage,
  viewHasNextPage,
  tableLoadingNextPage,
  viewLoadingNextPage,
  tableLoadMoreError,
  viewLoadMoreError,
  objectFilter,
  saving,
  nameError,
  requiredErrors,
  oracleConfirmation,
  rebuildAgentAssets,
  oracleSyncJob,
  oracleSyncSubmissionError,
  retryingOracleSync,
  deleting,
  onObjectFilterChange,
  onFormChange,
  onNameErrorClear,
  onRequiredErrorClear,
  onToggleTable,
  onToggleView,
  onToggleTableSchema,
  onToggleViewSchema,
  onLoadMoreTables,
  onLoadMoreViews,
  onRetryLoadMoreTables,
  onRetryLoadMoreViews,
  onSave,
  onDelete,
  onOracleConfirmationChange,
  onRebuildAgentAssetsChange,
  onRetryOracleSync,
}: {
  selectedProfile: Nl2SqlProfile | null;
  profileAccessProfile: ProfileAccessProfile | null;
  form: ProfileFormState;
  tableObjects: SchemaTable[];
  viewObjects: SchemaTable[];
  tableObjectTotal: number;
  viewObjectTotal: number;
  tableOwnerTotals: Record<string, number>;
  viewOwnerTotals: Record<string, number>;
  tableObjectsLoading: boolean;
  viewObjectsLoading: boolean;
  tableHasNextPage: boolean;
  viewHasNextPage: boolean;
  tableLoadingNextPage: boolean;
  viewLoadingNextPage: boolean;
  tableLoadMoreError: string;
  viewLoadMoreError: string;
  objectFilter: string;
  saving: boolean;
  nameError: ProfileNameError;
  requiredErrors: ProfileRequiredErrors;
  oracleConfirmation: string;
  rebuildAgentAssets: boolean;
  oracleSyncJob: ProfileSyncJobData | null;
  oracleSyncSubmissionError: string;
  retryingOracleSync: boolean;
  deleting: boolean;
  onObjectFilterChange: (value: string) => void;
  onFormChange: (updater: (current: ProfileFormState) => ProfileFormState) => void;
  onNameErrorClear: () => void;
  onRequiredErrorClear: (field: ProfileRequiredField) => void;
  onToggleTable: (name: string) => void;
  onToggleView: (name: string) => void;
  onToggleTableSchema: (owner: string, select: boolean) => Promise<void>;
  onToggleViewSchema: (owner: string, select: boolean) => Promise<void>;
  onLoadMoreTables: () => void;
  onLoadMoreViews: () => void;
  onRetryLoadMoreTables: () => void;
  onRetryLoadMoreViews: () => void;
  onSave: () => void;
  onDelete: () => void;
  onOracleConfirmationChange: (value: string) => void;
  onRebuildAgentAssetsChange: (value: boolean) => void;
  onRetryOracleSync: () => void;
}) {
  const oracleConfirmed = oracleConfirmation.trim() === "ADMIN_EXECUTE";
  const nameDescriptionId = nameError
    ? "profile-name-helper profile-name-error"
    : "profile-name-helper";
  const categoryDescriptionId = requiredErrors.category ? "profile-category-error" : undefined;
  return (
    <section className="grid min-w-0 content-start gap-4" aria-labelledby="profile-editor-heading">
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
          selectedProfile ? (
            <ObjectActionBar
              ariaLabel={t("profiles.editor.actions")}
              testId="profile-editor-actions"
              actions={[
                {
                  id: "delete",
                  label: t("profiles.action.delete"),
                  icon: Trash2,
                  tone: "danger",
                  loading: deleting,
                  onSelect: onDelete,
                },
              ]}
            />
          ) : undefined
        }
      />

      <section className="grid gap-3 rounded-md border border-border bg-background p-3">
        <h3 className="text-sm font-semibold text-foreground">{t("profiles.editor.basic")}</h3>
        <div className="grid gap-x-3 gap-y-1.5 md:grid-cols-2">
          <FieldLabel
            htmlFor="profile-name"
            label={t("profiles.field.name")}
            required
            className="order-1 md:order-none"
          />
          <FieldLabel
            htmlFor="profile-category"
            label={t("profiles.field.category")}
            required
            className="order-5 md:order-none"
          />
          <input
            id="profile-name"
            value={form.name}
            required
            aria-required="true"
            onChange={(event) => {
              const value = event.currentTarget.value.toUpperCase();
              onFormChange((current) => ({ ...current, name: value }));
              if (nameError) onNameErrorClear();
            }}
            onBlur={(event) => {
              const value = normalizeProfileName(event.currentTarget.value);
              onFormChange((current) => ({ ...current, name: value }));
            }}
            aria-invalid={Boolean(nameError)}
            aria-describedby={nameDescriptionId}
            className={`${inputClass} order-2 md:order-none ${
              nameError ? "border-danger focus:border-danger focus:ring-danger/40" : ""
            }`}
          />
          <input
            id="profile-category"
            value={form.category}
            required
            aria-required="true"
            aria-invalid={Boolean(requiredErrors.category)}
            aria-describedby={categoryDescriptionId}
            onChange={(event) => {
              const value = event.currentTarget.value;
              onFormChange((current) => ({ ...current, category: value }));
              if (requiredErrors.category) onRequiredErrorClear("category");
            }}
            className={`${inputClass} order-6 md:order-none ${
              requiredErrors.category
                ? "border-danger focus:border-danger focus:ring-danger/40"
                : ""
            }`}
          />
          <p
            id="profile-name-helper"
            className="order-3 text-xs font-normal leading-5 text-muted md:order-none md:col-span-2 md:whitespace-nowrap"
          >
            {t("profiles.field.nameHint")}
          </p>
          {nameError && (
            <div className="order-4 md:order-none md:col-span-2">
              <RequiredFieldError id="profile-name-error">
                {profileNameErrorMessage(nameError)}
              </RequiredFieldError>
            </div>
          )}
          {requiredErrors.category && (
            <div className="order-7 md:order-none md:col-start-2">
              <RequiredFieldError id="profile-category-error">
                {t("profiles.error.categoryRequired")}
              </RequiredFieldError>
            </div>
          )}
        </div>
      </section>

      {selectedProfile && profileAccessProfile ? (
        <section className="grid gap-2 rounded-md border border-border bg-background p-3">
          <h3 className="text-sm font-semibold text-foreground">{t("profiles.access.title")}</h3>
          <p className="text-sm text-muted">{t("profiles.access.hint")}</p>
          {profileAccessProfile.allowed_role_ids.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {profileAccessProfile.allowed_role_ids.map((roleId) => (
                <StatusBadge key={roleId} variant="neutral" label={roleId} />
              ))}
            </div>
          ) : (
            <p className="rounded-md border border-dashed border-border p-3 text-sm text-muted">
              {t("profiles.access.none")}
            </p>
          )}
        </section>
      ) : null}

      <section data-testid="profile-allowed-object-list" className="grid gap-3">
        <div>
          <h3 className="text-sm font-semibold text-foreground">{t("profiles.editor.objects")}</h3>
          <p className="mt-1 text-sm text-muted">{t("profiles.field.allowedObjects")}</p>
        </div>
        <DbObjectSelectorToolbar
          searchLabel={t("profiles.objects.filter")}
          searchPlaceholder={t("profiles.objects.filterPlaceholder")}
          searchValue={objectFilter}
          onSearchChange={onObjectFilterChange}
          resultLabel={t("objectSelector.resultCountWithSelected", {
            visible: tableObjects.length + viewObjects.length,
            total: tableObjectTotal + viewObjectTotal,
            selected: form.allowedTables.length + form.allowedViews.length,
          })}
          dataTestId="profile-object-search-toolbar"
        />
        <div className="grid gap-3 xl:grid-cols-2">
          <SchemaGroupedSelectionPanel
            title={t("profiles.objects.tablesTitle")}
            objects={tableObjects}
            totalCount={tableObjectTotal}
            selectedItems={form.allowedTables}
            dataTestId="profile-allowed-table-list"
            emptyTitle={t("profiles.objects.emptyTables")}
            emptyHint={t("profiles.objects.emptyTablesHint")}
            onToggle={onToggleTable}
            loading={tableObjectsLoading}
            hasNextPage={tableHasNextPage}
            loadingNextPage={tableLoadingNextPage}
            loadMoreError={tableLoadMoreError}
            onLoadMore={onLoadMoreTables}
            onRetryLoadMore={onRetryLoadMoreTables}
            ownerTotals={tableOwnerTotals}
            onToggleSchema={onToggleTableSchema}
          />
          <SchemaGroupedSelectionPanel
            title={t("profiles.objects.viewsTitle")}
            objects={viewObjects}
            totalCount={viewObjectTotal}
            selectedItems={form.allowedViews}
            dataTestId="profile-allowed-view-list"
            emptyTitle={t("profiles.objects.emptyViews")}
            emptyHint={t("profiles.objects.emptyViewsHint")}
            onToggle={onToggleView}
            loading={viewObjectsLoading}
            hasNextPage={viewHasNextPage}
            loadingNextPage={viewLoadingNextPage}
            loadMoreError={viewLoadMoreError}
            onLoadMore={onLoadMoreViews}
            onRetryLoadMore={onRetryLoadMoreViews}
            ownerTotals={viewOwnerTotals}
            onToggleSchema={onToggleViewSchema}
          />
        </div>
      </section>

      <SelectAiConfigFields
        form={form}
        setForm={onFormChange}
        requiredErrors={requiredErrors}
        onRequiredErrorClear={onRequiredErrorClear}
      />

      <section className="grid gap-3 rounded-md border border-border bg-card p-3" aria-labelledby="profile-engine-assets-heading">
        <div>
          <h3 id="profile-engine-assets-heading" className="text-sm font-semibold text-foreground">
            {t("profiles.oracle.assets.title")}
          </h3>
          <p className="mt-1 text-sm text-muted">{t("profiles.oracle.assets.hint")}</p>
        </div>
        <label className="flex min-h-11 items-center gap-2 rounded-md border border-border bg-background p-3 text-sm text-foreground">
          <input
            type="checkbox"
            checked={rebuildAgentAssets}
            onChange={(event) => onRebuildAgentAssetsChange(event.currentTarget.checked)}
            className="h-4 w-4 rounded border-border text-primary focus:ring-ring/40"
          />
          <span>{t("profiles.oracle.assets.refreshAgent")}</span>
        </label>
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

      <ProfileSaveResultRegion
        rebuildAgentAssets={rebuildAgentAssets}
        oracleSyncJob={oracleSyncJob}
        oracleSyncSubmissionError={oracleSyncSubmissionError}
        retryingOracleSync={retryingOracleSync}
        onRetryOracleSync={onRetryOracleSync}
      />
    </section>
  );
}

function ProfileSaveResultRegion({
  rebuildAgentAssets,
  oracleSyncJob,
  oracleSyncSubmissionError,
  retryingOracleSync,
  onRetryOracleSync,
}: {
  rebuildAgentAssets: boolean;
  oracleSyncJob: ProfileSyncJobData | null;
  oracleSyncSubmissionError: string;
  retryingOracleSync: boolean;
  onRetryOracleSync: () => void;
}) {
  if (!oracleSyncJob && !oracleSyncSubmissionError) {
    return null;
  }
  return (
    <section className="min-w-0" data-testid="profile-save-result-region">
      <ProfileSaveProgress
        job={oracleSyncJob}
        submissionError={oracleSyncSubmissionError}
        rebuildAgentAssets={rebuildAgentAssets}
        retrying={retryingOracleSync}
        onRetry={onRetryOracleSync}
      />
    </section>
  );
}

function dbProfileRefreshRequiredMessage(reasonCode: string, fallback = "") {
  if (reasonCode === "profile_list_refresh_target_unresolved") {
    return t("profiles.dbProfileRefresh.targetUnresolved");
  }
  if (reasonCode === "profile_list_refresh_submit_failed") {
    return t("profiles.dbProfileRefresh.submitFailed");
  }
  if (reasonCode === "profile_list_refresh_full_required") {
    return t("profiles.dbProfileRefresh.fullRequired");
  }
  return fallback || t("profiles.dbProfileRefresh.error");
}

function dbProfileRefreshProcessingLabel(job: SelectAiDbProfileRefreshJobData | null) {
  return job?.mode === "targeted"
    ? t("common.processing.dbProfileListDeltaSyncing")
    : t("common.processing.dbProfileListRefreshing");
}

function dbProfileRefreshIsActive(status: string) {
  return status === "pending" || status === "running";
}

export function ProfileManagementPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const confirm = useConfirm();
  const queryClient = useQueryClient();
  const auth = useAuth();
  const [form, setForm] = useState<ProfileFormState>(EMPTY_FORM);
  const [profileSearch, setProfileSearch] = useState("");
  const [objectFilter, setObjectFilter] = useState("");
  const [profileSort, setProfileSort] = useState<ProfileListSortState>({
    key: "name",
    direction: "asc",
  });
  const [oracleConfirmation, setOracleConfirmation] = useState("");
  const [rebuildAgentAssets, setRebuildAgentAssets] = useState(false);
  const [oracleSyncJobId, setOracleSyncJobId] = useState("");
  const [oracleSyncProfileId, setOracleSyncProfileId] = useState("");
  const [oracleSyncSubmissionError, setOracleSyncSubmissionError] = useState("");
  const reportedOracleSyncJobId = useRef("");
  const lastOracleConfirmationRef = useRef("");
  const [dbProfileRefreshJobId, setDbProfileRefreshJobId] = useState("");
  const [dbProfileRefreshError, setDbProfileRefreshError] = useState("");
  const [dbProfileRefreshNeedsFull, setDbProfileRefreshNeedsFull] = useState(false);
  const [loading, setLoading] = useState("");
  // message は初回ロード失敗の常設 Banner 専用(クエリ状態を監視する effect が所有する)。
  // 保存/削除の成否は toast、名前検証は nameError で扱う。
  const [message, setMessage] = useState("");
  // 手動更新の失敗は message とは別に保持する。同じ state に載せると、
  // 直後に走るクエリ監視 effect が空文字で上書きして警告が消えてしまう。
  const [refreshError, setRefreshError] = useState("");
  const [nameError, setNameError] = useState<ProfileNameError>(null);
  const [requiredErrors, setRequiredErrors] = useState<ProfileRequiredErrors>({});

  // ?profile= が唯一の情報源: null=一覧 / "new"=新規 / <id>=編集
  const profileParam = searchParams.get("profile");
  const syncJobParam = searchParams.get("syncJobId") ?? "";
  const activeView: ActiveView = profileParam ? "editor" : "list";
  const selectedProfileId = profileParam && profileParam !== "new" ? profileParam : "";
  // 1 打鍵ごとに一覧 / オブジェクト検索 API を叩かないよう、query key へはデバウンス値を渡す。
  const debouncedProfileSearch = useDebouncedValue(profileSearch, LIST_SEARCH_DEBOUNCE_MS);
  const debouncedObjectFilter = useDebouncedValue(objectFilter, LIST_SEARCH_DEBOUNCE_MS);
  const profilesQuery = useProfileSummaries(debouncedProfileSearch, profileSort);
  const profileDetailQuery = useProfileDetail(selectedProfileId);
  const tableObjectsQuery = useSchemaObjects(debouncedObjectFilter, "TABLE");
  const viewObjectsQuery = useSchemaObjects(debouncedObjectFilter, "VIEW");
  const schemaOwnersQuery = useSchemaOwners();
  const selectAiCredentialQuery = useSelectAiCredential();
  const schemaHeadQuery = useSchemaCatalogHead();
  const sharedSchemaRefresh = useSchemaRefreshCoordinator();
  const startDbProfileRefresh = useStartSelectAiDbProfileRefresh();
  const dbProfileRefreshJobQuery = useSelectAiDbProfileRefreshJob(dbProfileRefreshJobId);
  const dbProfileRefreshJob = dbProfileRefreshJobQuery.data ?? null;
  const dbProfileRefreshStatus = dbProfileRefreshJobQuery.isError
    ? "error"
    : (dbProfileRefreshJob?.status ?? "");
  const dbProfileRefreshing = dbProfileRefreshIsActive(dbProfileRefreshStatus);
  const dbProfilesQuery = useQuery({
    queryKey: ["nl2sql", "select-ai", "business-profiles"],
    queryFn: () => apiGet<SelectAiDbProfilesData>(BUSINESS_SELECT_AI_DB_PROFILES_URL),
    staleTime: 5_000,
  });
  const canViewProfileAccess = auth.hasPermission(MENU_PERMISSIONS.securityRoles);
  const profileAccessProfilesQuery = useQuery({
    queryKey: ["security", "profile-access", "profiles"],
    queryFn: () => securityApi.profileAccessProfiles(),
    enabled: canViewProfileAccess,
    staleTime: 10_000,
    retry: false,
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

  useEffect(() => {
    if (!syncJobParam) return;
    setOracleSyncJobId(syncJobParam);
    if (selectedProfileId) setOracleSyncProfileId(selectedProfileId);
  }, [selectedProfileId, syncJobParam]);
  const profiles = useMemo(
    () => profilesQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [profilesQuery.data]
  );
  const profilesLoaded = !profilesQuery.isPending;
  const selectedProfile = profileDetailQuery.data?.profile ?? null;
  // API 応答が想定外の形でも一覧画面全体を落とさない(配列以外は空扱い)。
  const profileAccessProfiles = Array.isArray(profileAccessProfilesQuery.data)
    ? profileAccessProfilesQuery.data
    : [];
  const selectedProfileAccessProfile =
    profileAccessProfiles.find((profile) => profile.id === selectedProfile?.id) ?? null;
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
  const tableObjectTotal = schemaObjectQueryTotal(tableObjectsQuery.data?.pages, tableObjects.length);
  const viewObjectTotal = schemaObjectQueryTotal(viewObjectsQuery.data?.pages, viewObjects.length);
  const profileTotal = schemaObjectQueryTotal(profilesQuery.data?.pages, profiles.length);
  const profileLoadMoreError =
    profilesQuery.isFetchNextPageError && profilesQuery.error
      ? listLoadMoreErrorMessage(profilesQuery.error, "profiles.error.load")
      : "";
  const tableLoadMoreError =
    tableObjectsQuery.isFetchNextPageError && tableObjectsQuery.error
      ? listLoadMoreErrorMessage(tableObjectsQuery.error, "profiles.error.load")
      : "";
  const viewLoadMoreError =
    viewObjectsQuery.isFetchNextPageError && viewObjectsQuery.error
      ? listLoadMoreErrorMessage(viewObjectsQuery.error, "profiles.error.load")
      : "";
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
  const selectProfile = (profile: ProfileSummary) => {
    setMessage("");
    setRefreshError("");
    setOracleSyncJobId("");
    setOracleSyncProfileId("");
    setOracleSyncSubmissionError("");
    lastOracleConfirmationRef.current = "";
    reportedOracleSyncJobId.current = "";
    setSearchParams({ profile: profile.id });
  };

  const load = async (announce = false) => {
    setLoading("load");
    setRefreshError("");
    const results = await Promise.allSettled([
      profilesQuery.refetch(),
      tableObjectsQuery.refetch(),
      viewObjectsQuery.refetch(),
      schemaHeadQuery.refetch(),
      schemaOwnersQuery.refetch(),
      dbProfilesQuery.refetch(),
    ]);
    const succeeded = results.every(
      (result) => result.status === "fulfilled" && !result.value.isError
    );
    if (!succeeded) {
      setRefreshError(t("profiles.error.load"));
    } else if (announce) {
      toast.success(t("common.action.refreshed"));
    }
    setLoading("");
  };

  const runSchemaRefresh = async () => {
    try {
      await sharedSchemaRefresh.start();
    } catch {
      // 共通 Coordinator が失敗状態と Toast を一度だけ管理する。
    }
  };

  const runDbProfileRefresh = async () => {
    try {
      const job = await startDbProfileRefresh.mutateAsync();
      setDbProfileRefreshError("");
      setDbProfileRefreshNeedsFull(false);
      setDbProfileRefreshJobId(job.job_id);
      queryClient.setQueryData(nl2sqlIncrementalKeys.selectAiDbProfileRefreshJob(job.job_id), job);
    } catch (err) {
      const message =
        err instanceof Error ? err.message : t("profiles.dbProfileRefresh.error");
      setDbProfileRefreshError(message);
      setDbProfileRefreshNeedsFull(true);
      toastError(message);
    }
  };

  const trackDbProfileRefreshSignal = useCallback(
    (result: DbProfileRefreshSignal | null | undefined) => {
      const jobId = result?.profile_list_refresh_job_id ?? "";
      if (jobId) {
        setDbProfileRefreshError("");
        setDbProfileRefreshNeedsFull(false);
        setDbProfileRefreshJobId(jobId);
        return true;
      }
      if (result?.profile_list_refresh_required) {
        setDbProfileRefreshError(
          dbProfileRefreshRequiredMessage(result.profile_list_refresh_reason_code ?? "")
        );
        setDbProfileRefreshNeedsFull(true);
        return true;
      }
      return false;
    },
    []
  );

  useEffect(() => {
    const job = dbProfileRefreshJobQuery.data;
    if (!job) return;
    if (job.status === "done") {
      setDbProfileRefreshNeedsFull(false);
      setDbProfileRefreshError("");
      void queryClient.invalidateQueries({ queryKey: ["nl2sql", "select-ai"] });
      toast.success(
        t("profiles.dbProfileRefresh.done", {
          changed: job.changed_profiles,
          deleted: job.deleted_profiles,
        })
      );
    } else if (job.status === "error") {
      const message = dbProfileRefreshRequiredMessage(job.error_code, job.error_message);
      setDbProfileRefreshError(message);
      setDbProfileRefreshNeedsFull(job.requires_full_refresh || Boolean(job.error_code));
      toastError(message);
    }
  }, [dbProfileRefreshJobQuery.data?.status, queryClient]);

  useEffect(() => {
    if (!dbProfileRefreshJobQuery.isError) return;
    const message =
      dbProfileRefreshJobQuery.error instanceof Error
        ? dbProfileRefreshJobQuery.error.message
        : t("profiles.dbProfileRefresh.error");
    setDbProfileRefreshError(message);
    setDbProfileRefreshNeedsFull(true);
  }, [dbProfileRefreshJobQuery.error, dbProfileRefreshJobQuery.isError]);

  // 編集対象の切替時にフォームと編集付帯 state を同期する(deep link 初回ロード後も含む)
  const editTargetKey = selectedProfile?.id ?? (profileParam === "new" ? "new" : "");
  const formInitializationKey =
    editTargetKey === "new" && selectAiCredentialQuery.isPending ? "" : editTargetKey;
  useEffect(() => {
    if (!formInitializationKey) return;
    setForm(
      selectedProfile
        ? profileToForm(selectedProfile)
        : emptyProfileForm(selectAiCredentialQuery.data?.region)
    );
    setOracleConfirmation("");
    setNameError(null);
    setRequiredErrors({});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [formInitializationKey]);

  useEffect(() => {
    const job = oracleSyncJobQuery.data;
    if (!job || !["succeeded", "failed", "cancelled"].includes(job.status)) return;
    if (reportedOracleSyncJobId.current === job.job_id) return;
    reportedOracleSyncJobId.current = job.job_id;
    if (job.status === "succeeded") {
      const trackingRefresh = trackDbProfileRefreshSignal(job.oracle_result);
      if (!trackingRefresh) {
        void queryClient.invalidateQueries({ queryKey: ["nl2sql", "select-ai"] });
      }
      toast.success(t("profiles.oracle.sync.succeeded"));
    }
  }, [oracleSyncJobQuery.data, queryClient, trackDbProfileRefreshSignal]);

  // 無効な id(削除済み等)の deep link は一覧へ縮退
  useEffect(() => {
    if (selectedProfileId && profileDetailQuery.isError) {
      setSearchParams({}, { replace: true });
    }
  }, [profileDetailQuery.isError, selectedProfileId, setSearchParams]);

  useEffect(() => {
    const error =
      (profilesQuery.error && !profilesQuery.data ? profilesQuery.error : null) ??
      (tableObjectsQuery.error && !tableObjectsQuery.data ? tableObjectsQuery.error : null) ??
      (viewObjectsQuery.error && !viewObjectsQuery.data ? viewObjectsQuery.error : null) ??
      (schemaHeadQuery.error && !schemaHeadQuery.data ? schemaHeadQuery.error : null);
    setMessage(error instanceof Error ? error.message : "");
  }, [
    profilesQuery.data,
    profilesQuery.error,
    schemaHeadQuery.data,
    schemaHeadQuery.error,
    tableObjectsQuery.data,
    tableObjectsQuery.error,
    viewObjectsQuery.data,
    viewObjectsQuery.error,
  ]);

  // legacy hash 導線: 旧 #profile-learning は Select AI 設定へ正規化する
  useEffect(() => {
    if (location.hash !== "#profile-learning") return;
    if (!profileParam && !profilesLoaded) return;
    const target = profiles.find((profile) => !profile.archived)?.id ?? "new";
    navigate(
      {
        pathname: location.pathname,
        search: profileParam ? location.search : `?profile=${target}`,
        hash: "#profile-select-ai",
      },
      { replace: true }
    );
  }, [
    location.hash,
    location.pathname,
    location.search,
    navigate,
    profileParam,
    profiles,
    profilesLoaded,
  ]);

  useEffect(() => {
    if (location.hash !== "#profile-select-ai" || profileParam || !profilesLoaded) return;
    const target = profiles.find((profile) => !profile.archived)?.id ?? "new";
    navigate(
      { pathname: location.pathname, search: `?profile=${target}`, hash: location.hash },
      { replace: true }
    );
  }, [location.hash, location.pathname, navigate, profileParam, profiles, profilesLoaded]);

  useEffect(() => {
    if (location.hash !== "#profile-select-ai" || activeView !== "editor") return;
    const frame = window.requestAnimationFrame(() => {
      const target = document.getElementById("profile-select-ai");
      target?.scrollIntoView({ block: "start", inline: "nearest", behavior: "auto" });
      target?.focus({ preventScroll: true });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [activeView, location.hash, selectedProfile?.id]);

  const startNew = () => {
    setMessage("");
    setRefreshError("");
    setOracleSyncJobId("");
    setOracleSyncProfileId("");
    setOracleSyncSubmissionError("");
    lastOracleConfirmationRef.current = "";
    reportedOracleSyncJobId.current = "";
    setSearchParams({ profile: "new" });
  };

  // dirty 判定: 読み込み時と同じ変換を再計算して比較する(追加 state 不要)
  const isDirty = useMemo(() => {
    const baseline = selectedProfile
      ? profileToForm(selectedProfile)
      : emptyProfileForm(selectAiCredentialQuery.data?.region);
    return JSON.stringify(form) !== JSON.stringify(baseline);
  }, [form, selectedProfile, selectAiCredentialQuery.data?.region]);

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
      return { ...current, [key]: toggleObjectSelection(current[key], name) };
    });
  };

  const toggleSchemaSnapshot = async (
    kind: "table" | "view",
    owner: string,
    select: boolean
  ) => {
    const key = kind === "table" ? "allowedTables" : "allowedViews";
    // 表示中の一覧と同じ条件で一括操作するため、デバウンス後の値を使う。
    const filter = debouncedObjectFilter.trim();
    const filtered = Boolean(filter);
    try {
      // フィルタ適用中は「表示されている(=ヒットした)object」だけを一括対象にする。
      // 解除もスキーマ全体へ波及させないため、この場合は snapshot が必要。
      const snapshot =
        select || filtered
          ? await getSchemaObjectSnapshot(owner, kind === "table" ? "TABLE" : "VIEW", filter)
          : [];
      setForm((current) => ({
        ...current,
        [key]: applySchemaBulkSelection({
          current: current[key],
          snapshot,
          ownerPrefix: `${owner}.`,
          select,
          filtered,
        }),
      }));
    } catch (error) {
      toastError(error instanceof Error ? error.message : t("profiles.error.load"));
    }
  };

  const save = async () => {
    const nextNameError = profileNameError(form.name);
    const nextRequiredErrors = profileRequiredErrors(form);
    setNameError(nextNameError);
    setRequiredErrors(nextRequiredErrors);
    if (nextNameError || hasProfileRequiredErrors(nextRequiredErrors)) {
      return;
    }
    setNameError(null);
    setRequiredErrors({});
    setOracleSyncJobId("");
    setOracleSyncSubmissionError("");
    reportedOracleSyncJobId.current = "";
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
      setOracleSyncProfileId(saved.id);
      setRequiredErrors({});
      if (!selectedProfile) {
        setSearchParams({ profile: saved.id }, { replace: true });
      }
      toast.success(t("profiles.message.saved"));
    } catch (err) {
      if (isProfileNameConflictError(err)) {
        setNameError("duplicate");
        window.requestAnimationFrame(() => document.getElementById("profile-name")?.focus());
        setLoading("");
        return;
      }
      toastError(err instanceof Error ? err.message : t("profiles.error.save"));
      setLoading("");
      return;
    }

    try {
      setOracleSyncSubmissionError("");
      const submittedOracleConfirmation = oracleConfirmation.trim();
      lastOracleConfirmationRef.current = submittedOracleConfirmation;
      const job = await apiPost<ProfileSyncJobData>(
        `/api/nl2sql/profiles/${saved.id}/oracle-sync-jobs`,
        {
          confirmation: submittedOracleConfirmation,
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
      setOracleSyncProfileId(job.profile_id);
      queryClient.setQueryData(["nl2sql", "oracle-sync-job", job.job_id], job);
    } catch (err) {
      setOracleSyncSubmissionError(
        err instanceof Error ? err.message : t("profiles.oracle.sync.failed")
      );
    } finally {
      setLoading("");
    }
  };

  const retryOracleSync = async () => {
    const profileId = oracleSyncJob?.profile_id || oracleSyncProfileId || selectedProfile?.id;
    if (!profileId) return;
    setLoading("retry-oracle-sync");
    try {
      const retryConfirmation =
        oracleConfirmation.trim() || lastOracleConfirmationRef.current.trim();
      const job = oracleSyncJob?.status === "failed"
        ? await apiPost<ProfileSyncJobData>(
            `/api/nl2sql/oracle-sync-jobs/${oracleSyncJob.job_id}/retry`
          )
        : await apiPost<ProfileSyncJobData>(
            `/api/nl2sql/profiles/${profileId}/oracle-sync-jobs`,
            {
              confirmation: retryConfirmation,
              reason: "ui-profile-management-retry",
              rebuild_agent_assets: rebuildAgentAssets,
            },
            { headers: { "Idempotency-Key": `profile-retry-${profileId}-${Date.now()}` } }
          );
      lastOracleConfirmationRef.current = retryConfirmation;
      setOracleSyncSubmissionError("");
      reportedOracleSyncJobId.current = "";
      setOracleSyncJobId(job.job_id);
      setOracleSyncProfileId(job.profile_id);
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
      const deleted = await apiDelete<ProfileDeleteData>(
        `/api/nl2sql/profiles/${encodeURIComponent(profile.id)}`,
        { "If-Match": `"${profile.etag || profileDetailQuery.data?.etag || ""}"` }
      );
      const deletedProfile = deleted.profile;
      queryClient.removeQueries({ queryKey: nl2sqlIncrementalKeys.profile(deletedProfile.id) });
      await queryClient.invalidateQueries({ queryKey: ["nl2sql", "profiles", "search"] });
      const trackingRefresh = deleted.oracle_cleanup.some((item) =>
        trackDbProfileRefreshSignal(item)
      );
      if (!trackingRefresh) {
        await queryClient.invalidateQueries({ queryKey: ["nl2sql", "select-ai"] });
      }
      if (profileParam) {
        setSearchParams({}, { replace: true });
      }
      const cleanupWarnings = deleted.oracle_cleanup.filter((item) => item.warning.trim());
      const cleanupExecuted = deleted.oracle_cleanup.some((item) => item.executed);
      toast.success(
        t(
          cleanupExecuted && cleanupWarnings.length === 0
            ? "profiles.message.deletedWithOracleCleanup"
            : "profiles.message.deleted",
          { name: deletedProfile.name }
        )
      );
      if (cleanupWarnings.length > 0) {
        toast.warning(
          t("profiles.message.oracleCleanupWarning", { count: cleanupWarnings.length })
        );
      }
    } catch (err) {
      const fallback =
        err instanceof ApiError && err.status === 502
          ? t("profiles.error.deleteOracleCleanup")
          : t("profiles.error.delete");
      toastError(err instanceof Error && err.message ? err.message : fallback);
    } finally {
      setLoading("");
    }
  };

  const toggleSort = (key: ProfileListSortKey) => {
    setProfileSort((current) => ({
      key,
      direction: current.key === key && current.direction === "asc" ? "desc" : "asc",
    }));
  };
  const clearRequiredError = useCallback((field: ProfileRequiredField) => {
    setRequiredErrors((current) => {
      if (!current[field]) return current;
      const next = { ...current };
      delete next[field];
      return next;
    });
  }, []);

  const editor = (
    <ProfileEditor
      selectedProfile={selectedProfile}
      profileAccessProfile={selectedProfileAccessProfile}
      form={form}
      tableObjects={tableObjects}
      viewObjects={viewObjects}
      tableObjectTotal={tableObjectTotal}
      viewObjectTotal={viewObjectTotal}
      tableOwnerTotals={tableOwnerTotals}
      viewOwnerTotals={viewOwnerTotals}
      tableObjectsLoading={tableObjectsQuery.isPending}
      viewObjectsLoading={viewObjectsQuery.isPending}
      tableHasNextPage={Boolean(tableObjectsQuery.hasNextPage)}
      viewHasNextPage={Boolean(viewObjectsQuery.hasNextPage)}
      tableLoadingNextPage={tableObjectsQuery.isFetchingNextPage}
      viewLoadingNextPage={viewObjectsQuery.isFetchingNextPage}
      tableLoadMoreError={tableLoadMoreError}
      viewLoadMoreError={viewLoadMoreError}
      objectFilter={objectFilter}
      saving={loading === "save"}
      nameError={nameError}
      requiredErrors={requiredErrors}
      oracleConfirmation={oracleConfirmation}
      rebuildAgentAssets={rebuildAgentAssets}
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
      onRetryLoadMoreTables={() => void tableObjectsQuery.fetchNextPage()}
      onRetryLoadMoreViews={() => void viewObjectsQuery.fetchNextPage()}
      onNameErrorClear={() => setNameError(null)}
      onRequiredErrorClear={clearRequiredError}
      onSave={() => void save()}
      onDelete={() => {
        if (selectedProfile) void deleteProfile(selectedProfile);
      }}
      onOracleConfirmationChange={setOracleConfirmation}
      onRebuildAgentAssetsChange={setRebuildAgentAssets}
      onRetryOracleSync={() => void retryOracleSync()}
    />
  );
  const schemaRefreshing = sharedSchemaRefresh.isRefreshing;
  const profileListRefreshing = profilesQuery.isFetching && !profilesQuery.isFetchingNextPage;
  const profileWorkspaceProcessing = schemaRefreshing ? (
      <SchemaRefreshProcessing testId="profile-management-workspace-processing" />
    ) : loading === "load" || profileListRefreshing || dbProfileRefreshing ? (
      <ProcessingIndicator
        active
        label={
          dbProfileRefreshing
            ? dbProfileRefreshProcessingLabel(dbProfileRefreshJob)
            : t("common.processing.refreshing")
        }
        operationKey={
          dbProfileRefreshing
            ? dbProfileRefreshJobId || "db-profile-refresh"
            : "profile-refresh"
        }
        placement="workspace"
        className="rounded-md border border-border bg-background px-3 py-2"
        testId="profile-management-workspace-processing"
        activityIcon="none"
      />
    ) : undefined;
  const showProfileWorkspaceProcessing =
    Boolean(profileWorkspaceProcessing) &&
    (profiles.length > 0 || loading === "load" || schemaRefreshing || dbProfileRefreshing);
  const headerDbProfileRefreshStatus =
    dbProfileRefreshing || dbProfileRefreshStatus === "error" ? dbProfileRefreshStatus : "";
  const headerRefreshStatus = headerDbProfileRefreshStatus;
  const workspaceNotice = dbProfileRefreshError
    ? { tone: "danger" as const, message: dbProfileRefreshError }
    : sharedSchemaRefresh.error
      ? { tone: "danger" as const, message: sharedSchemaRefresh.error }
    : refreshError
      ? { tone: "danger" as const, message: `${refreshError} ${t("profiles.error.retryHint")}` }
    : message
      ? { tone: "danger" as const, message: `${message} ${t("profiles.error.retryHint")}` }
      : null;
  const workspaceNoticeAction =
    dbProfileRefreshError && dbProfileRefreshNeedsFull ? (
      <Button
        type="button"
        variant="secondary"
        size="sm"
        loading={startDbProfileRefresh.isPending || dbProfileRefreshing}
        onClick={() => void runDbProfileRefresh()}
      >
        <RefreshCw size={15} aria-hidden="true" />
        <span>{t("profiles.action.dbProfileRefresh")}</span>
      </Button>
    ) : (
      <Button type="button" variant="secondary" size="sm" onClick={() => void load()}>
        <RefreshCw size={15} aria-hidden="true" />
        <span>{t("profiles.action.refresh")}</span>
      </Button>
    );

  return (
    <>
      <PageHeader
        title={t("nav.profiles")}
        subtitle={t("profiles.subtitle")}
        status={
          activeView === "list" && (schemaRefreshing || sharedSchemaRefresh.error) ? (
            <SchemaRefreshHeaderStatus testId="profile-management-schema-refresh-status" />
          ) : activeView === "list" && headerRefreshStatus ? (
            <PageHeaderStatusBadge
              variant={headerRefreshStatus === "error" ? "danger" : "info"}
              label={t(`profiles.dbProfileRefresh.status.${headerRefreshStatus}`)}
            />
          ) : undefined
        }
        actions={
          activeView === "list"
            ? [
                {
                  id: "create-profile",
                  kind: "primary",
                  label: t("profiles.action.new"),
                  icon: Plus,
                  onClick: startNew,
                },
                {
                  id: "refresh",
                  kind: "utility",
                  label: t("common.action.refresh"),
                  icon: RefreshCw,
                  onClick: () => load(true),
                  loading: loading === "load" || profileListRefreshing,
                },
                {
                  id: "schema-refresh",
                  kind: "utility",
                  label: t("common.action.schemaRefresh"),
                  icon: RefreshCw,
                  onClick: runSchemaRefresh,
                  loading: schemaRefreshing,
                  disabled: schemaRefreshing,
                },
                {
                  id: "db-profile-refresh",
                  kind: "utility",
                  label: t("profiles.action.dbProfileRefresh"),
                  icon: RefreshCw,
                  onClick: runDbProfileRefresh,
                  loading: dbProfileRefreshing || startDbProfileRefresh.isPending,
                  disabled: dbProfileRefreshing || startDbProfileRefresh.isPending,
                },
              ]
            : []
        }
        actionsTestId="profile-management-actions"
      />

      <main className="grid gap-4 p-4 lg:p-8">
        <PageNotice
          notice={workspaceNotice}
          action={workspaceNoticeAction}
        />

        {activeView === "list" ? (
          <DbObjectManagementPanelShell
              id="profile-management-panel-list"
              role="region"
              idPrefix="profile-management"
              ariaLabel={t("profiles.workspace.label")}
              processing={showProfileWorkspaceProcessing ? profileWorkspaceProcessing : undefined}
            >
              <ProfileList
                profiles={profiles}
                totalCount={profileTotal}
                selectedProfileId={selectedProfileId}
                loading={!profilesLoaded || (loading === "load" && profiles.length === 0)}
                search={profileSearch}
                sort={profileSort}
                onSearchChange={setProfileSearch}
                onSortChange={toggleSort}
                onSelect={selectProfile}
                hasNextPage={Boolean(profilesQuery.hasNextPage)}
                loadingNextPage={profilesQuery.isFetchingNextPage}
                loadMoreError={profileLoadMoreError}
                onLoadMore={() => void profilesQuery.fetchNextPage()}
                onRetryLoadMore={() => void profilesQuery.fetchNextPage()}
              />
            </DbObjectManagementPanelShell>
        ) : (
          <>
            <div>
              <Button type="button" variant="ghost" size="sm" onClick={() => void backToList()}>
                <ArrowLeft size={15} aria-hidden="true" />
                <span>{t("profiles.action.backToList")}</span>
              </Button>
            </div>
            <DbObjectManagementPanelShell
              id="profile-management-panel-editor"
              role="region"
              idPrefix="profile-management"
              ariaLabel={selectedProfile ? t("profiles.editor.edit") : t("profiles.editor.new")}
              processing={profileWorkspaceProcessing}
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
            </DbObjectManagementPanelShell>
          </>
        )}
      </main>
    </>
  );
}
