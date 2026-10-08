import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
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
  LoadMoreFooter,
  DataTable,
  EmptyState,
  FormStatus,
  toast,
  StatusBadge,
  PageHeader,
  PageBody,
  useConfirm,
  apiErrorMessage,
  SelectField,
  TextField,
  type SelectFieldOption,
  ClearActionButton,
  ProcessingIndicator,
  ObjectActionBar,
  TimedLoadingState,
  TableSkeleton,
  FormSkeleton,
  ExecutionConfirmationField,
  INFORMATION_TABLE_ROW_CLASS,
  INFORMATION_TABLE_VISIBLE_ROWS,
  RowTitleButton,
  ListPicker,
  type ListPickerGroup,
  type ListPickerItem,
  TextareaField,
} from "@engchina/production-ready-ui";
import { ErrorState } from "@/components/StateViews";
import { IdentifierText } from "@/components/IdentifierText";

import { PageHeaderStatusBadge } from "@/components/PageHeaderStatusBadge";
import { PageNotice } from "@/components/page-notice";
import { ApiError, apiDelete, apiGet, apiPatch, apiPost } from "@/lib/api";
import { useValuesChanged } from "@/lib/render-sync";
import { t } from "@/lib/i18n";
import { appPath } from "@/lib/base-path";
import { listLoadMoreErrorMessage } from "@/lib/load-more-error";
import { useUnsavedChangesGuard } from "@/lib/useUnsavedChangesGuard";
import { useSchemaOwners } from "@/lib/queries";
import { useAuth } from "@/features/security/AuthProvider";
import { MENU_PERMISSIONS } from "@/features/security/menu-permissions";
import { securityApi } from "@/features/security/api";
import type { ProfileAccessProfile } from "@/features/security/types";
import {
  DbManagementSearchField,
  DbObjectManagementPanelShell,
  DbObjectPanelHeader,
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
  countSelectedObjectsInOwner,
  normalizeObjectKey,
  profileFormEquals,
  selectedObjectKeys,
  toggleObjectSelection,
} from "../profileObjectSelection";
import type { ProfileListSortKey, ProfileListSortState } from "../profileListState";
import { BUSINESS_SELECT_AI_DB_PROFILES_URL } from "../selectAiProfileUrls";
import { schemaTableQualifiedName } from "../workbenchState";
import { DbObjectName } from "../components/DbObjectName";
import { formatDbObjectName, formatDbObjectPart } from "../dbObjectIdentity";
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
const SELECT_AI_DEFAULT_REGION = "us-chicago-1";
const SELECT_AI_REGION_OPTIONS = [
  { value: "us-chicago-1", label: "us-chicago-1" },
  { value: "ap-osaka-1", label: "ap-osaka-1" },
] as const satisfies readonly SelectFieldOption<string>[];
const PROFILE_NAME_PATTERN = /^[A-Z][A-Z0-9_]*$/u;
/** ヘッダーの「スキーマを更新」の起点（SchemaRefreshCoordinator の start(origin)。#821）。 */
const PROFILE_SCHEMA_REFRESH_HEADER = "profile-management-header";

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
  model: "xai.grok-4.6",
  embedding_model: "cohere.embed-v4.0",
  max_tokens: SELECT_AI_MAX_TOKENS_MAX,
  enforce_object_list: true,
  comments: true,
  // ANNOTATIONS / ドメイン継承 annotation を Select AI の prompt へ渡す(既定 false だと効かない)。
  annotations: true,
  constraints: true,
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

function emptyProfileForm(): ProfileFormState {
  return {
    ...EMPTY_FORM,
    allowedTables: [],
    allowedViews: [],
    selectAiConfig: { ...DEFAULT_SELECT_AI_CONFIG },
  };
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

function normalizeProfile(profile: Nl2SqlProfile): Nl2SqlProfile {
  const selectAiConfig = { ...DEFAULT_SELECT_AI_CONFIG, ...profile.select_ai_config };
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
  const selectAiConfig: ProfileSelectAiConfig = {
    ...normalized.select_ai_config,
    additional_instructions: mergeAdditionalInstructions(
      normalized.select_ai_config.additional_instructions,
      normalized.sql_rules
    ),
  };
  // 旧名の印（previous_profile_name）は名称の変更の Oracle 反映のために backend が付け外しする値で、
  // 利用者は編集しない（PATCH も受け付けない）。フォームに持つと、反映の job が印を消した後の
  // 最新版と比べて「未保存の変更」に見えるため、フォームと未保存の判定から外す。
  delete selectAiConfig.previous_profile_name;
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
    qualified_name: formatDbObjectName({ owner: object.owner, name: object.object_name }),
    logical_name: object.logical_name,
    owner: object.owner,
    table_type: object.object_type,
    comment: object.comment,
    row_count: object.row_count,
    columns: [],
    constraints: [],
  };
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
  loadingIndicator = true,
  search,
  sort,
  onSearchChange,
  onSortChange,
  onSelect,
  profileHref,
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
  /** 読み込み中の文言と経過時間を一覧の位置に出すか。作業領域の先頭に同じ処理の表示があるときは false。 */
  loadingIndicator?: boolean;
  search: string;
  sort: ProfileListSortState;
  onSearchChange: (value: string) => void;
  onSortChange: (key: ProfileListSortKey) => void;
  onSelect: (profile: ProfileSummary) => void;
  /** プロファイルの編集画面の URL（名前のリンク。新しいタブで開ける。#583）。 */
  profileHref: (profile: ProfileSummary) => string;
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
      />
      {/* 一覧の toolbar。検索欄だけを行全体に伸ばさず、件数と 2:1 で同じ行に置く。 */}
      <div className="grid gap-2 rounded-md border border-border bg-surface-sunken p-3 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] lg:items-end lg:gap-x-6">
        <DbManagementSearchField
          label={t("profiles.list.search")}
          placeholder={t("profiles.list.searchPlaceholder")}
          value={search}
          onChange={onSearchChange}
        />
        <div className="flex min-h-10 items-center lg:justify-end">
          <StatusBadge icon={false} variant="info" label={t("profiles.objects.count", { count: totalCount })} />
        </div>
      </div>
      {loading ? (
        loadingIndicator ? (
          <TimedLoadingState
            label={t("profiles.summary.loading")}
            operationKey="profile-list-load"
            framed={false}
            testId="profile-list-loading"
          >
            <TableSkeleton columns={3} testId="profile-list-skeleton" />
          </TimedLoadingState>
        ) : (
          // 同じ読み込みの経過時間は作業領域の先頭に出ているため、ここでは形だけを出す（messaging.md §3.7）。
          <TableSkeleton columns={3} testId="profile-list-skeleton" />
        )
      ) : profiles.length === 0 ? (
        <EmptyState
          title={search.trim() ? t("profiles.list.noResultsTitle") : t("profiles.empty.title")}
          hint={search.trim() ? t("profiles.list.noResultsHint") : t("profiles.empty.hint")}
        />
      ) : (
        <DataTable
          columns={[
            {
              key: "name",
              header: t("profiles.field.name"),
              sortable: true,
              className: "align-top",
              render: (profile) => (
                <RowTitleButton
                  // 375px では名前列が狭く、区切りのない名前（PROFILE_EMP 等）の min-content が隣の列へはみ出すため、
                  // `_` の位置を優先して折り返し、最後の手段として任意位置で折り返す。
                  title={<IdentifierText value={profile.name} />}
                  subtitle={<span className="line-clamp-2">{profile.category || "-"}</span>}
                  current={profile.id === selectedProfileId}
                  aria-label={t("profiles.action.selectProfile", { name: profile.name })}
                  href={profileHref(profile)}
                  onClick={() => onSelect(profile)}
                />
              ),
            },
            {
              key: "tables",
              header: t("profiles.field.allowedTables"),
              sortable: true,
              align: "right",
              headerClassName: "w-[7rem]",
              className: "align-top font-sans",
              render: (profile) => profile.allowed_table_count,
            },
            {
              key: "views",
              header: t("profiles.field.allowedViews"),
              sortable: true,
              align: "right",
              headerClassName: "w-[7rem]",
              className: "align-top font-sans",
              render: (profile) => profile.allowed_view_count,
            },
          ]}
          rows={profiles}
          getRowKey={(profile) => profile.id}
          sort={sort}
          onSortChange={(next) => onSortChange(next.key as ProfileListSortKey)}
          selectedRowKey={selectedProfileId}
          onRowClick={onSelect}
          rowProps={() => ({ className: INFORMATION_TABLE_ROW_CLASS })}
          testId="profile-management-grid"
          tableClassName="w-full min-w-0 table-fixed"
          // 名前・カテゴリの 2 行セルが並ぶ一覧。横スクロールは出さず、縦だけ内部スクロールにする。
          // 高さは md 未満 5 行・md 以上 8 行の実測（#403。以前は手書きの 20rem / 30.5rem）。
          className="max-w-full overflow-x-hidden"
          scrollAriaLabel={t("profiles.list.scrollLabel")}
          scrollTestId="profile-management-list"
          stickyHeader
          visibleRows={INFORMATION_TABLE_VISIBLE_ROWS}
        />
      )}
      {/* 「さらに読み込む」と失敗の再試行は共通の LoadMoreFooter（#1266）。件数は見出しのバッジで出すので summary は空。 */}
      {!loading && profiles.length > 0 && (hasNextPage || loadMoreError) && (
        <LoadMoreFooter
          summary=""
          hasMore={hasNextPage}
          loadingMore={loadingNextPage}
          loadMoreError={loadMoreError || undefined}
          onLoadMore={onLoadMore}
          onRetry={onRetryLoadMore}
          loadMoreLabel={t("profiles.action.loadMore")}
          retryLabel={t("common.retry")}
          testId="profile-management-load-more"
        />
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
      className="grid scroll-mt-4 gap-3 rounded-md border border-border bg-surface-sunken p-3"
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
        />
        <TextField
          id="profile-select-ai-model"
          label={t("profiles.field.model")}
          required
          value={form.selectAiConfig.model}
          error={requiredErrors.model ? t("profiles.error.modelRequired") : undefined}
          onValueChange={(value) => {
            updateSelectAiConfig(setForm, { model: value });
            if (requiredErrors.model) onRequiredErrorClear("model");
          }}
          className="min-w-0"
        />
        <TextField
          id="profile-select-ai-max-tokens"
          label={t("profiles.field.maxTokens")}
          required
          type="number"
          min={SELECT_AI_MAX_TOKENS_MIN}
          max={SELECT_AI_MAX_TOKENS_MAX}
          step={1}
          value={form.selectAiConfig.max_tokens}
          error={requiredErrors.maxTokens ? t("profiles.error.maxTokensRequired") : undefined}
          onValueChange={(value) => {
            updateSelectAiConfig(setForm, { max_tokens: parseMaxTokensInput(value) });
            if (requiredErrors.maxTokens) onRequiredErrorClear("maxTokens");
          }}
          onBlur={(event) =>
            updateSelectAiConfig(setForm, {
              max_tokens: normalizeSelectAiMaxTokens(event.currentTarget.value),
            })
          }
          className="min-w-0"
        />
        <TextField
          id="profile-select-ai-embedding-model"
          label={t("profiles.field.embeddingModel")}
          required
          value={form.selectAiConfig.embedding_model}
          error={
            requiredErrors.embeddingModel ? t("profiles.error.embeddingModelRequired") : undefined
          }
          onValueChange={(value) => {
            updateSelectAiConfig(setForm, { embedding_model: value });
            if (requiredErrors.embeddingModel) onRequiredErrorClear("embeddingModel");
          }}
          className="min-w-0"
        />
      </div>
      <div className="grid gap-2 md:grid-cols-4">
        {([
          ["enforce_object_list", "profiles.field.enforceObjectList"],
          ["comments", "profiles.field.comments"],
          ["annotations", "profiles.field.annotations"],
          ["constraints", "profiles.field.constraints"],
        ] as const).map(([key, labelKey]) => (
          <label key={key} className="flex min-h-11 items-center gap-2 rounded-md border border-border bg-surface p-3 text-sm text-fg">
            <input
              type="checkbox"
              checked={Boolean(form.selectAiConfig[key])}
              onChange={(event) => updateSelectAiConfig(setForm, { [key]: event.currentTarget.checked })}
              className="h-4 w-4 rounded border-border text-accent-fg"
            />
            <span>{t(labelKey)}</span>
          </label>
        ))}
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <TextareaField
          id="profile-select-ai-role"
          label={t("profiles.field.role")}
          helper={t("profiles.field.roleHint")}
          value={form.selectAiConfig.role}
          rows={6}
          onChange={(event) => updateSelectAiConfig(setForm, { role: event.currentTarget.value })}
          placeholder={t("profiles.placeholder.role")}
        />
        <TextareaField
          id="profile-select-ai-additional-instructions"
          label={t("profiles.field.additionalInstructions")}
          helper={t("profiles.field.additionalInstructionsHint")}
          value={form.selectAiConfig.additional_instructions}
          rows={6}
          onChange={(event) =>
            updateSelectAiConfig(setForm, {
              additional_instructions: event.currentTarget.value,
            })
          }
          placeholder={t("profiles.placeholder.additionalInstructions")}
        />
      </div>
    </section>
  );
}

/**
 * 許可する表・ビューの選択（#600）。大量の候補から複数を選ぶ共通の `ListPicker` に、スキーマ（owner）ごとのグループと
 * スキーマ単位の一括選択を渡す。検索欄は表・ビューで共通のツールバー（`DbObjectSelectorToolbar`）が持つ。
 * 行の描画・仮想スクロール・キーボード操作・追加読み込みのフッターは ListPicker が持つ（NL2SQL で二重に持たない）。
 */
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
  disabled,
  hasActiveFilter,
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
  disabled: boolean;
  hasActiveFilter: boolean;
  hasNextPage: boolean;
  loadingNextPage: boolean;
  loadMoreError: string;
  onLoadMore: () => void;
  onRetryLoadMore: () => void;
  ownerTotals: Record<string, number>;
  onToggleSchema: (owner: string, select: boolean) => Promise<void>;
}) {
  const selectedSet = useMemo(() => selectedObjectKeys(selectedItems), [selectedItems]);
  const { items, owners } = useMemo(() => {
    const grouped = new Map<string, SchemaTable[]>();
    for (const object of objects) {
      // 小文字を含む owner（`"Sales"`）を大文字の同名 owner と同じグループにしない（#563）。
      grouped.set(object.owner, [...(grouped.get(object.owner) ?? []), object]);
    }
    const sortedOwners = [...grouped.keys()].sort((left, right) => left.localeCompare(right));
    const pickerItems: ListPickerItem[] = sortedOwners.flatMap((owner) =>
      (grouped.get(owner) ?? [])
        .slice()
        .sort((left, right) => schemaTableQualifiedName(left).localeCompare(schemaTableQualifiedName(right)))
        .map((object) => {
          const qualified = schemaTableQualifiedName(object);
          return {
            key: normalizeObjectKey(qualified),
            label: <DbObjectName value={qualified} size="xs" truncate className="block" />,
            textValue: qualified,
            description: object.logical_name || object.comment || object.table_name,
            groupKey: owner,
          };
        })
    );
    return { items: pickerItems, owners: sortedOwners };
  }, [objects]);

  const groups: ListPickerGroup[] = owners.map((owner) => {
    const selectedCount = countSelectedObjectsInOwner(selectedSet, owner);
    const total = ownerTotals[owner] ?? items.filter((item) => item.groupKey === owner).length;
    // 表示・読み上げは SQL と同じ表記（`"Sales"` と `SALES` を見分けられる、#563）。
    const ownerLabel = formatDbObjectPart(owner);
    return {
      key: owner,
      label: (
        <span className="rounded border border-border bg-surface px-2 py-0.5 font-mono text-xs font-semibold text-fg">
          {ownerLabel}
        </span>
      ),
      textValue: ownerLabel,
      countLabel: t("profiles.objects.schemaCount", { selected: selectedCount, total }),
      onSelectAll: () => onToggleSchema(owner, true),
      onClearAll: () => onToggleSchema(owner, false),
      selectAllDisabled: total > 0 && selectedCount >= total,
      clearAllDisabled: selectedCount === 0,
      testId: `${ownerLabel.toLowerCase()}-schema`,
    };
  });

  return (
    <ListPicker
      id={dataTestId}
      title={title}
      headingLevel={4}
      label={title}
      items={items}
      groups={groups}
      selectedKeys={selectedSet}
      onToggle={(item) => onToggle(item.textValue)}
      total={totalCount}
      hasActiveFilter={hasActiveFilter}
      loading={loading}
      disabled={disabled}
      hasMore={hasNextPage}
      loadingMore={loadingNextPage}
      loadMoreError={loadMoreError || undefined}
      onLoadMore={loadMoreError ? onRetryLoadMore : onLoadMore}
      fixedHeight
      labels={{
        resultCount: ({ visible, total, selected }) =>
          t("objectSelector.resultCountWithSelected", { visible, total, selected }),
        loading: t("profiles.objects.loading"),
        loadMore: t("profiles.action.loadMore"),
        retry: t("common.retry"),
        emptyTitle,
        emptyHint,
        noResultsTitle: emptyTitle,
        noResultsHint: emptyHint,
        clearSearch: t("common.clearSearch"),
        groupSelectAll: t("profiles.objects.selectSchemaAction"),
        groupClearAll: t("profiles.objects.clearSchema"),
        groupSelectAllAria: (name) => t("common.selection.selectGroup", { name }),
        groupClearAllAria: (name) => t("common.selection.clearGroup", { name }),
        keyboardHint: t("objectSelector.keyboardHint"),
      }}
      testId={dataTestId}
    />
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
  busy,
  nameError,
  requiredErrors,
  oracleConfirmation,
  rebuildAgentAssets,
  canClearOracleExecution,
  oracleSyncJob,
  oracleSyncSubmissionError,
  saveError,
  saveConflict,
  reloadingLatest,
  onReloadLatest,
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
  onOracleExecutionClear,
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
  busy: boolean;
  nameError: ProfileNameError;
  requiredErrors: ProfileRequiredErrors;
  oracleConfirmation: string;
  rebuildAgentAssets: boolean;
  canClearOracleExecution: boolean;
  oracleSyncJob: ProfileSyncJobData | null;
  oracleSyncSubmissionError: string;
  /** プロファイルの保存（PATCH / POST）の失敗。保存ボタンの直下だけに出す（messaging.md §3.3.1。#585）。 */
  saveError: string;
  /** 保存の失敗がほかの更新との競合（409）。失敗の文の下に「最新の内容を読み込む」を出す（#1111）。 */
  saveConflict: boolean;
  reloadingLatest: boolean;
  onReloadLatest: () => void;
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
  onOracleExecutionClear: () => void;
  onRebuildAgentAssetsChange: (value: boolean) => void;
  onRetryOracleSync: () => void;
}) {
  const oracleConfirmed = oracleConfirmation.trim() === "ADMIN_EXECUTE";
  return (
    <fieldset disabled={busy} className="grid min-w-0 content-start gap-4" aria-labelledby="profile-editor-heading">
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

      <section className="grid gap-3 rounded-md border border-border bg-surface-sunken p-3">
        <h3 className="text-sm font-semibold text-fg">{t("profiles.editor.basic")}</h3>
        <div className="grid gap-x-3 gap-y-1.5 md:grid-cols-2">
          {/* 名称の補足は 2 列（名称・カテゴリ）にまたがる 1 行で出すため、欄の helper ではなく下の行に置き、
              aria-describedby で名称の欄につなぐ。狭い画面では名称 → 補足 → カテゴリの順に積む（#631）。 */}
          <TextField
            id="profile-name"
            label={t("profiles.field.name")}
            required
            value={form.name}
            onChange={(event) => {
              const value = event.currentTarget.value.toUpperCase();
              onFormChange((current) => ({ ...current, name: value }));
              if (nameError) onNameErrorClear();
            }}
            onBlur={(event) => {
              const value = normalizeProfileName(event.currentTarget.value);
              onFormChange((current) => ({ ...current, name: value }));
            }}
            error={nameError ? profileNameErrorMessage(nameError) : undefined}
            aria-describedby="profile-name-helper"
            className="order-1 min-w-0 md:order-none"
          />
          <TextField
            id="profile-category"
            label={t("profiles.field.category")}
            required
            value={form.category}
            onChange={(event) => {
              const value = event.currentTarget.value;
              onFormChange((current) => ({ ...current, category: value }));
              if (requiredErrors.category) onRequiredErrorClear("category");
            }}
            error={requiredErrors.category ? t("profiles.error.categoryRequired") : undefined}
            className="order-3 min-w-0 md:order-none"
          />
          <p
            id="profile-name-helper"
            className="order-2 text-xs font-normal leading-5 text-fg-muted md:order-none md:col-span-2 md:whitespace-nowrap"
          >
            {t("profiles.field.nameHint")}
          </p>
        </div>
      </section>

      {selectedProfile && profileAccessProfile ? (
        <section className="grid gap-2 rounded-md border border-border bg-surface-sunken p-3">
          <h3 className="text-sm font-semibold text-fg">{t("profiles.access.title")}</h3>
          <p className="text-sm text-fg-muted">{t("profiles.access.hint")}</p>
          {profileAccessProfile.allowed_role_ids.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {profileAccessProfile.allowed_role_ids.map((roleId) => (
                <StatusBadge icon={false} key={roleId} variant="neutral" label={roleId} />
              ))}
            </div>
          ) : (
            <p className="rounded-md border border-dashed border-border p-3 text-sm text-fg-muted">
              {t("profiles.access.none")}
            </p>
          )}
        </section>
      ) : null}

      <section data-testid="profile-allowed-object-list" className="grid gap-3">
        <div>
          <h3 className="text-sm font-semibold text-fg">{t("profiles.editor.objects")}</h3>
          <p className="mt-1 text-sm text-fg-muted">{t("profiles.field.allowedObjects")}</p>
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
            disabled={busy}
            hasActiveFilter={Boolean(objectFilter.trim())}
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
            disabled={busy}
            hasActiveFilter={Boolean(objectFilter.trim())}
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

      <section className="grid gap-3 rounded-md border border-border bg-surface p-3" aria-labelledby="profile-engine-assets-heading">
        <div>
          <h3 id="profile-engine-assets-heading" className="text-sm font-semibold text-fg">
            {t("profiles.oracle.assets.title")}
          </h3>
          <p className="mt-1 text-sm text-fg-muted">{t("profiles.oracle.assets.hint")}</p>
        </div>
        <label className="flex min-h-11 items-center gap-2 rounded-md border border-border bg-surface-sunken p-3 text-sm text-fg">
          <input
            type="checkbox"
            checked={rebuildAgentAssets}
            onChange={(event) => onRebuildAgentAssetsChange(event.currentTarget.checked)}
            className="h-4 w-4 rounded border-border text-accent-fg"
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
          <>
            <Button
              type="button"
              variant="primary"
              size="lg"
              loading={saving}
              disabled={!oracleConfirmed || saving}
              onClick={onSave} icon={Save}>
              <span>{t("profiles.action.save")}</span>
            </Button>
            <ClearActionButton
              label={t("profiles.oracle.actions.clear")}
              size="lg"
              className="w-full sm:w-auto"
              disabled={!canClearOracleExecution || saving}
              onClick={onOracleExecutionClear}
            />
          </>
        }
      />

      {/* 保存ボタンはフォームの中（確認語と並ぶ）なので、欄に結び付かない保存の失敗はボタンの直下の
          FormStatus の 1 か所だけに出す（Toast に重ねない。messaging.md §3.3.1。#585）。 */}
      {saveError ? (
        <div data-testid="profile-save-error" className="grid justify-items-start gap-2">
          <FormStatus tone="danger" message={saveError} />
          {saveConflict ? (
            <Button
              type="button"
              variant="secondary"
              icon={RefreshCw}
              loading={reloadingLatest}
              onClick={onReloadLatest}
            >
              {t("profiles.conflict.reload")}
            </Button>
          ) : null}
        </div>
      ) : null}
      <ProfileSaveResultRegion
        rebuildAgentAssets={rebuildAgentAssets}
        oracleSyncJob={oracleSyncJob}
        oracleSyncSubmissionError={oracleSyncSubmissionError}
        retryingOracleSync={retryingOracleSync}
        onRetryOracleSync={onRetryOracleSync}
      />
    </fieldset>
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
  const [profileSaveError, setProfileSaveError] = useState("");
  // 完了を通知済みの Oracle 同期 job ID。render 中に比べるため state で持つ。
  const [reportedOracleSyncJobId, setReportedOracleSyncJobId] = useState("");
  // 終了した Oracle 同期 job（成功の通知と再取得は effect で行う）。
  const [finishedOracleSyncJob, setFinishedOracleSyncJob] = useState<{
    jobId: string;
    profileId: string;
    succeeded: boolean;
    invalidateSelectAi: boolean;
  } | null>(null);
  const lastOracleConfirmationRef = useRef("");
  const [dbProfileRefreshJobId, setDbProfileRefreshJobId] = useState("");
  const [dbProfileRefreshError, setDbProfileRefreshError] = useState("");
  const [dbProfileRefreshNeedsFull, setDbProfileRefreshNeedsFull] = useState(false);
  const [loading, setLoading] = useState("");
  // 「表示を更新」をどこで押したか（ヘッダー / 失敗の案内）。スピナーは押したボタンだけが出す（#819）。
  const [loadOrigin, setLoadOrigin] = useState<"header" | "notice">("header");
  // message は初回ロード失敗の常設 Banner 専用(クエリ状態を監視する effect が所有する)。
  // 保存/削除の成否は toast、名前検証は nameError で扱う。
  const [message, setMessage] = useState("");
  // 手動更新の失敗は message とは別に保持する。同じ state に載せると、
  // 直後に走るクエリ監視 effect が空文字で上書きして警告が消えてしまう。
  const [refreshError, setRefreshError] = useState("");
  const [nameError, setNameError] = useState<ProfileNameError>(null);
  const [requiredErrors, setRequiredErrors] = useState<ProfileRequiredErrors>({});
  // 保存の失敗がほかの更新との競合（ETag の不一致の 409）か。文に合わせて「最新の内容を読み込む」を出す（#1111）。
  const [profileSaveConflict, setProfileSaveConflict] = useState(false);

  // ?profile= が唯一の情報源: null=一覧 / "new"=新規 / <id>=編集
  const profileParam = searchParams.get("profile");
  const editTargetRef = useRef(profileParam);
  // 最新の編集対象を commit 時に入れる（render 中に ref を書かない）。
  useLayoutEffect(() => { editTargetRef.current = profileParam; });
  const [bulkSelecting, setBulkSelecting] = useState(false);
  const mutationBusy = bulkSelecting || (loading !== "" && loading !== "load");
  // 名称の重複で保存が失敗したら名称の欄へフォーカスを戻す。保存中は編集の fieldset が disabled なので、
  // 保存の状態が解けて欄が有効になってから移す（requestAnimationFrame だと再描画の前に走り、無効の欄に当たる）。
  const [nameFocusRequest, setNameFocusRequest] = useState(0);
  useEffect(() => {
    if (nameFocusRequest === 0 || mutationBusy) return;
    document.getElementById("profile-name")?.focus();
  }, [nameFocusRequest, mutationBusy]);
  const mutationBusyRef = useRef(mutationBusy);
  // 最新の実行中状態を commit 時に入れる（render 中に ref を書かない）。handler は直前に true を先に入れる。
  useLayoutEffect(() => { mutationBusyRef.current = mutationBusy; });
  const syncJobParam = searchParams.get("syncJobId") ?? "";
  const activeView: ActiveView = profileParam ? "editor" : "list";
  const selectedProfileId = profileParam && profileParam !== "new" ? profileParam : "";
  // 1 打鍵ごとに一覧 / オブジェクト検索 API を叩かないよう、query key へはデバウンス値を渡す。
  // 検索語・所有者の接頭辞は SearchField が確定した値（入力が止まって 300ms・Enter・消去。IME の変換中は
  // 確定しない）なので、ここでは遅延させずにそのまま問い合わせに使う（#535）。
  const profilesQuery = useProfileSummaries(profileSearch, profileSort);
  const profileDetailQuery = useProfileDetail(selectedProfileId);
  const tableObjectsQuery = useSchemaObjects(objectFilter, "TABLE");
  const viewObjectsQuery = useSchemaObjects(objectFilter, "VIEW");
  const schemaOwnersQuery = useSchemaOwners();
  const schemaHeadQuery = useSchemaCatalogHead();
  const sharedSchemaRefresh = useSchemaRefreshCoordinator();
  const startDbProfileRefresh = useStartSelectAiDbProfileRefresh();
  const dbProfileRefreshJobQuery = useSelectAiDbProfileRefreshJob(dbProfileRefreshJobId);
  const dbProfileRefreshJob = dbProfileRefreshJobQuery.data ?? null;
  const dbProfileRefreshStatus = dbProfileRefreshJobQuery.isError
    ? "error"
    : (dbProfileRefreshJob?.status ?? "");
  const dbProfileRefreshing = dbProfileRefreshIsActive(dbProfileRefreshStatus);
  // どの「業務プロファイルを再取得」で始めた送信か（ヘッダー / 失敗の案内）。送信の間だけ押した側を回す（#821）。
  const [dbProfileRefreshOrigin, setDbProfileRefreshOrigin] = useState<"header" | "notice">("header");
  const dbProfilesQuery = useQuery({
    queryKey: ["nl2sql", "select-ai", "business-profiles"],
    queryFn: () => apiGet<SelectAiDbProfilesData>(BUSINESS_SELECT_AI_DB_PROFILES_URL),
    staleTime: 5_000,
  });
  // 利用可能ロールの一覧 API は権限管理（menu.security_permissions）の担当（#206）。
  const canViewProfileAccess = auth.hasPermission(MENU_PERMISSIONS.securityPermissions);
  // 候補は全件を読まない（#608）。選択中の業務プロファイルだけを ids で読み、利用できるロールを出す。
  const profileAccessProfilesQuery = useQuery({
    queryKey: ["security", "profile-access", "profiles", selectedProfileId],
    queryFn: ({ signal }) =>
      securityApi.profileAccessProfiles(
        { q: "", limit: 1, offset: 0, ids: [selectedProfileId] },
        { signal }
      ),
    enabled: canViewProfileAccess && Boolean(selectedProfileId),
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

  // URL の syncJobId を追跡対象へ render 中に反映する。
  if (useValuesChanged([selectedProfileId, syncJobParam]) && syncJobParam) {
    setOracleSyncJobId(syncJobParam);
    if (selectedProfileId) setOracleSyncProfileId(selectedProfileId);
  }
  const profiles = useMemo(
    () => profilesQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [profilesQuery.data]
  );
  const profilesLoaded = !profilesQuery.isPending;
  const selectedProfile = profileDetailQuery.data?.profile ?? null;
  // API 応答が想定外の形でも一覧画面全体を落とさない(配列以外は空扱い)。
  const profileAccessProfiles = Array.isArray(profileAccessProfilesQuery.data?.items)
    ? profileAccessProfilesQuery.data.items
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
          item.owner,
          item.table_count,
        ])
      ),
    [schemaOwnersQuery.data]
  );
  const viewOwnerTotals = useMemo(
    () =>
      Object.fromEntries(
        (schemaOwnersQuery.data?.owners ?? []).map((item) => [
          item.owner,
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
    setProfileSaveError("");
    lastOracleConfirmationRef.current = "";
    setReportedOracleSyncJobId("");
    setSearchParams({ profile: profile.id });
  };

  const load = async (announce = false, origin: "header" | "notice" = "header") => {
    setLoading("load");
    setLoadOrigin(origin);
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
      await sharedSchemaRefresh.start(PROFILE_SCHEMA_REFRESH_HEADER);
    } catch {
      // 共通 Coordinator が失敗状態と Toast を一度だけ管理する。
    }
  };

  const runDbProfileRefresh = async (origin: "header" | "notice") => {
    setDbProfileRefreshOrigin(origin);
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
      toast.error(message);
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

  // job の状態は render 中に state へ反映し、再取得と通知は effect で行う。
  if (useValuesChanged([dbProfileRefreshJobQuery.data?.status, queryClient]) && dbProfileRefreshJobQuery.data) {
    const job = dbProfileRefreshJobQuery.data;
    if (job.status === "done") {
      setDbProfileRefreshNeedsFull(false);
      setDbProfileRefreshError("");
    } else if (job.status === "error") {
      setDbProfileRefreshError(dbProfileRefreshRequiredMessage(job.error_code, job.error_message));
      setDbProfileRefreshNeedsFull(job.requires_full_refresh || Boolean(job.error_code));
    }
  }
  // 通知は job の status が変わったときだけ出す（ポーリングで data が作り直されても重ねない）。
  // 最新の job は commit 時に ref へ入れ、effect ではその ref を読む。
  const dbProfileRefreshJobRef = useRef(dbProfileRefreshJobQuery.data);
  useLayoutEffect(() => {
    dbProfileRefreshJobRef.current = dbProfileRefreshJobQuery.data;
  });
  const dbProfileRefreshJobStatus = dbProfileRefreshJobQuery.data?.status;
  useEffect(() => {
    const job = dbProfileRefreshJobRef.current;
    if (!job) return;
    if (job.status === "done") {
      void queryClient.invalidateQueries({ queryKey: ["nl2sql", "select-ai"] });
      // 1 文目は何が起きたかだけにし、件数は description に分ける（#899）。
      toast.success(t("profiles.dbProfileRefresh.done"), {
        description: t("profiles.dbProfileRefresh.doneDetail", {
          changed: job.changed_profiles,
          deleted: job.deleted_profiles,
        }),
      });
    } else if (job.status === "error") {
      toast.error(dbProfileRefreshRequiredMessage(job.error_code, job.error_message));
    }
  }, [dbProfileRefreshJobStatus, queryClient]);

  if (useValuesChanged([dbProfileRefreshJobQuery.error, dbProfileRefreshJobQuery.isError]) && dbProfileRefreshJobQuery.isError) {
    const message =
      dbProfileRefreshJobQuery.error instanceof Error
        ? dbProfileRefreshJobQuery.error.message
        : t("profiles.dbProfileRefresh.error");
    setDbProfileRefreshError(message);
    setDbProfileRefreshNeedsFull(true);
  }

  // 編集対象の切替時にフォームと編集付帯 state を同期する(deep link 初回ロード後も含む)
  const editTargetKey = selectedProfile?.id ?? (profileParam === "new" ? "new" : "");
  const formInitializationKey = editTargetKey;
  if (useValuesChanged([formInitializationKey]) && formInitializationKey) {
    setForm(
      selectedProfile
        ? profileToForm(selectedProfile)
        : emptyProfileForm()
    );
    setOracleConfirmation("");
    setNameError(null);
    setRequiredErrors({});
  }

  // 終了した Oracle 同期 job を render 中に一度だけ state へ反映し、通知と再取得は effect で行う。
  if (useValuesChanged([oracleSyncJobQuery.data, queryClient, trackDbProfileRefreshSignal])) {
    const job = oracleSyncJobQuery.data;
    if (
      job &&
      ["succeeded", "failed", "cancelled"].includes(job.status) &&
      reportedOracleSyncJobId !== job.job_id
    ) {
      setReportedOracleSyncJobId(job.job_id);
      const succeeded = job.status === "succeeded";
      const trackingRefresh = succeeded ? trackDbProfileRefreshSignal(job.oracle_result) : false;
      setFinishedOracleSyncJob({
        jobId: job.job_id,
        profileId: job.profile_id,
        succeeded,
        invalidateSelectAi: succeeded && !trackingRefresh,
      });
    }
  }
  useEffect(() => {
    if (!finishedOracleSyncJob) return;
    // 反映の job は名称の変更の後始末で業務プロファイルを更新し、ETag を進める（旧名の印の消去）。
    // 成功・失敗のどちらでも最新版を取り直し、次の保存の If-Match が古い ETag で 409 にならないようにする。
    void queryClient.invalidateQueries({
      queryKey: nl2sqlIncrementalKeys.profile(finishedOracleSyncJob.profileId),
    });
    if (!finishedOracleSyncJob.succeeded) return;
    if (finishedOracleSyncJob.invalidateSelectAi) {
      void queryClient.invalidateQueries({ queryKey: ["nl2sql", "select-ai"] });
    }
    toast.success(t("profiles.oracle.sync.succeeded"));
  }, [finishedOracleSyncJob, queryClient]);



  // 一覧・スキーマの読込エラーを render 中にメッセージへ反映する。
  if (
    useValuesChanged([
      profilesQuery.data,
      profilesQuery.error,
      schemaHeadQuery.data,
      schemaHeadQuery.error,
      tableObjectsQuery.data,
      tableObjectsQuery.error,
      viewObjectsQuery.data,
      viewObjectsQuery.error,
    ])
  ) {
    const error =
      (profilesQuery.error && !profilesQuery.data ? profilesQuery.error : null) ??
      (tableObjectsQuery.error && !tableObjectsQuery.data ? tableObjectsQuery.error : null) ??
      (viewObjectsQuery.error && !viewObjectsQuery.data ? viewObjectsQuery.error : null) ??
      (schemaHeadQuery.error && !schemaHeadQuery.data ? schemaHeadQuery.error : null);
    setMessage(error instanceof Error ? error.message : "");
  }

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
    setProfileSaveError("");
    setProfileSaveConflict(false);
    lastOracleConfirmationRef.current = "";
    setReportedOracleSyncJobId("");
    setSearchParams({ profile: "new" });
  };

  // dirty 判定: 読み込み時と同じ変換を再計算して比較する(追加 state 不要)。
  // 許可オブジェクトは選択順で配列が入れ替わるため集合として比較する。
  const isDirty = useMemo(() => {
    const baseline = selectedProfile
      ? profileToForm(selectedProfile)
      : emptyProfileForm();
    return !profileFormEquals(form, baseline);
  }, [form, selectedProfile]);

  const confirmDiscard = useCallback(
    () =>
      confirm({
        title: t("profiles.discard.confirm.title"),
        description: t("profiles.discard.confirm.description"),
        confirmLabel: t("profiles.discard.confirm.confirm"),
        tone: "danger",
        dismissOnOverlay: false,
      }),
    [confirm]
  );

  // 「一覧に戻る」だけでなく、サイドナビ等のルート遷移とタブを閉じる操作も保護する。
  useUnsavedChangesGuard(activeView === "editor" && isDirty, confirmDiscard);

  const backToList = async () => {
    if (mutationBusyRef.current) return;
    if (isDirty && !(await confirmDiscard())) return;
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
    if (mutationBusyRef.current) return;
    const target = profileParam;
    mutationBusyRef.current = true;
    setBulkSelecting(true);
    const key = kind === "table" ? "allowedTables" : "allowedViews";
    // 表示中の一覧と同じ条件で一括操作するため、デバウンス後の値を使う。
    const filter = objectFilter.trim();
    const filtered = Boolean(filter);
    try {
      // フィルタ適用中は「表示されている(=ヒットした)object」だけを一括対象にする。
      // 解除もスキーマ全体へ波及させないため、この場合は snapshot が必要。
      const snapshot =
        select || filtered
          ? await getSchemaObjectSnapshot(owner, kind === "table" ? "TABLE" : "VIEW", filter)
          : [];
      if (editTargetRef.current !== target) return;
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
      if (editTargetRef.current === target) toast.error(error instanceof Error ? error.message : t("profiles.error.load"));
    } finally {
      setBulkSelecting(false);
    }
  };

  const save = async () => {
    if (mutationBusyRef.current) return;
    const target = profileParam;
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
    setProfileSaveError("");
    setProfileSaveConflict(false);
    setReportedOracleSyncJobId("");
    mutationBusyRef.current = true;
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
      if (editTargetRef.current === target) {
        setForm(profileToForm(normalizeProfile(saved)));
        setOracleSyncProfileId(saved.id);
        setRequiredErrors({});
        // 破壊的操作のゲートなので、保存が通ったら必ず再入力を求める
        // (既存 profile の保存では編集対象が変わらず初期化 effect が走らない)。
        setOracleConfirmation("");
        if (!selectedProfile) {
          setSearchParams({ profile: saved.id }, { replace: true });
        }
      }
      // 1 文目は何が起きたかだけにし、Oracle Profile の反映の続きは description に分ける（#899）。
      toast.success(t("profiles.message.saved"), {
        description: t("profiles.message.savedOracleSyncing"),
      });
    } catch (err) {
      if (isProfileNameConflictError(err)) {
        setNameError("duplicate");
        setNameFocusRequest((current) => current + 1);
        setLoading("");
        return;
      }
      if (editTargetRef.current === target) {
        // 既存の業務プロファイルの更新で、名称の重複以外の 409 は、ほかの保存で版（ETag）が進んだ競合。
        const conflict = Boolean(selectedProfile) && err instanceof ApiError && err.status === 409;
        setProfileSaveConflict(conflict);
        setProfileSaveError(
          conflict
            ? t("profiles.error.saveConflict")
            : err instanceof Error && err.message
              ? err.message
              : t("profiles.error.save")
        );
      }
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
      setReportedOracleSyncJobId("");
      if (editTargetRef.current === target) {
        setOracleSyncJobId(job.job_id);
        setOracleSyncProfileId(job.profile_id);
      }
      queryClient.setQueryData(["nl2sql", "oracle-sync-job", job.job_id], job);
    } catch (err) {
      setOracleSyncSubmissionError(
        err instanceof Error ? err.message : t("profiles.oracle.sync.failed")
      );
    } finally {
      setLoading("");
    }
  };

  // 競合した保存の後に、最新の版を読み直して編集の内容を置き換える（未保存の変更は確認の後に破棄する）。
  const reloadLatestProfile = async () => {
    if (mutationBusyRef.current || !selectedProfile) return;
    const target = profileParam;
    const ok = await confirm({
      title: t("profiles.conflict.reload.confirm.title"),
      description: t("profiles.conflict.reload.confirm.description"),
      confirmLabel: t("profiles.conflict.reload.confirm.confirm"),
      tone: "danger",
      dismissOnOverlay: false,
    });
    if (!ok || editTargetRef.current !== target) return;
    mutationBusyRef.current = true;
    setLoading("reload-latest");
    try {
      const latest = await profileDetailQuery.refetch();
      if (editTargetRef.current !== target) return;
      if (latest.isError || !latest.data) {
        setProfileSaveError(apiErrorMessage(latest.error, t("profiles.error.load")));
        return;
      }
      setForm(profileToForm(latest.data.profile));
      setOracleConfirmation("");
      setNameError(null);
      setRequiredErrors({});
      setProfileSaveError("");
      setProfileSaveConflict(false);
    } finally {
      setLoading("");
    }
  };

  const retryOracleSync = async () => {
    if (mutationBusyRef.current) return;
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
      setReportedOracleSyncJobId("");
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
    if (mutationBusyRef.current) return;
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
      if (editTargetRef.current === profile.id) {
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
      toast.error(err instanceof Error && err.message ? err.message : fallback);
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
  const canClearOracleExecution = Boolean(
    oracleConfirmation ||
      rebuildAgentAssets ||
      oracleSyncJobId ||
      oracleSyncProfileId ||
      oracleSyncJob ||
      oracleSyncSubmissionError ||
      syncJobParam
  );

  const clearOracleExecution = () => {
    setOracleConfirmation("");
    setRebuildAgentAssets(false);
    setOracleSyncJobId("");
    setOracleSyncProfileId("");
    setOracleSyncSubmissionError("");
    setProfileSaveError("");
    lastOracleConfirmationRef.current = "";
    setReportedOracleSyncJobId("");
    if (syncJobParam) {
      const nextParams = new URLSearchParams(searchParams);
      nextParams.delete("syncJobId");
      setSearchParams(nextParams, { replace: true });
    }
  };

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
      busy={mutationBusy}
      nameError={nameError}
      requiredErrors={requiredErrors}
      oracleConfirmation={oracleConfirmation}
      rebuildAgentAssets={rebuildAgentAssets}
      canClearOracleExecution={canClearOracleExecution}
      oracleSyncJob={oracleSyncJob}
      oracleSyncSubmissionError={oracleSyncSubmissionError}
      saveError={profileSaveError}
      saveConflict={profileSaveConflict}
      reloadingLatest={loading === "reload-latest"}
      onReloadLatest={() => void reloadLatestProfile()}
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
      onOracleExecutionClear={clearOracleExecution}
      onRebuildAgentAssetsChange={setRebuildAgentAssets}
      onRetryOracleSync={() => void retryOracleSync()}
    />
  );
  const schemaRefreshing = sharedSchemaRefresh.isRefreshing;
  // スキーマの更新・業務プロファイルの再取得は durable job。開始のボタンは押した側だけを送信の間 loading にし、
  // job の間は無効にするだけ。job の間のスピナーは一覧の上の進行の表示が 1 つだけ出す（messaging §3.7、#821）。
  const headerSchemaRefreshStarting = sharedSchemaRefresh.startingOrigin === PROFILE_SCHEMA_REFRESH_HEADER;
  const dbProfileRefreshStarting = startDbProfileRefresh.isPending;
  const dbProfileRefreshBusy = dbProfileRefreshing || dbProfileRefreshStarting;
  const headerDbProfileRefreshStarting = dbProfileRefreshStarting && dbProfileRefreshOrigin === "header";
  const noticeDbProfileRefreshStarting = dbProfileRefreshStarting && dbProfileRefreshOrigin === "notice";
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
        className="rounded-md border border-border bg-surface-sunken px-3 py-2"
        testId="profile-management-workspace-processing"
        // 押した「表示を更新」が回っている間はスピナーを出さない。定期・他の操作の後の一覧の取り直しと、
        // 業務プロファイルの再取得の job の間はボタンを回さないため、この表示がスピナーを出す（#819 / #821）。
        activityIcon={loading === "load" && !dbProfileRefreshing ? "none" : "spinner"}
      />
    ) : undefined;
  const showProfileWorkspaceProcessing =
    Boolean(profileWorkspaceProcessing) &&
    (profiles.length > 0 || loading === "load" || schemaRefreshing || dbProfileRefreshing);
  const profileListLoading = !profilesLoaded || (loading === "load" && profiles.length === 0);
  // 初回の読込は一覧の読込表示がスピナーを出す。ヘッダーの「表示を更新」は回さない（狭い画面では「その他の操作」の
  // 中で見えない。同じ処理のスピナーは 1 つ。messaging §3.7、#416）。
  const profileListShowsSpinner = profileListLoading && !showProfileWorkspaceProcessing;
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
        loading={noticeDbProfileRefreshStarting}
        disabled={dbProfileRefreshBusy && !noticeDbProfileRefreshStarting}
        onClick={() => void runDbProfileRefresh("notice")} icon={RefreshCw}>
        <span>{t("profiles.action.dbProfileRefresh")}</span>
      </Button>
    ) : (
      <Button
        type="button"
        variant="secondary"
        size="sm"
        // 押した再読込の間だけ回す。ヘッダーの「表示を更新」の再読込の間は無効にするだけ（#819）。
        loading={loading === "load" && loadOrigin === "notice"}
        disabled={mutationBusy || (loading === "load" && loadOrigin !== "notice")}
        onClick={() => void load(false, "notice")}
        icon={RefreshCw}
      >
        <span>{t("profiles.action.refresh")}</span>
      </Button>
    );

  return (
    <>
      <PageHeader wide
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
                  onClick: () => load(true, "header"),
                  // 押した再読込の間だけ回す。定期・他の操作の後の一覧の取り直し（profileListRefreshing）と、
                  // 失敗の案内の「表示を更新」の再読込では回さない（#819）。
                  loading: loading === "load" && loadOrigin === "header" && !profileListShowsSpinner,
                  disabled: profileListShowsSpinner || (loading === "load" && loadOrigin !== "header"),
                },
                {
                  id: "schema-refresh",
                  kind: "utility",
                  label: t("common.action.schemaRefresh"),
                  icon: RefreshCw,
                  onClick: runSchemaRefresh,
                  loading: headerSchemaRefreshStarting,
                  disabled: schemaRefreshing && !headerSchemaRefreshStarting,
                },
                {
                  id: "db-profile-refresh",
                  kind: "utility",
                  label: t("profiles.action.dbProfileRefresh"),
                  icon: RefreshCw,
                  onClick: () => runDbProfileRefresh("header"),
                  loading: headerDbProfileRefreshStarting,
                  disabled: dbProfileRefreshBusy && !headerDbProfileRefreshStarting,
                },
              ]
            : []
        }
        actionsTestId="profile-management-actions"
        // 編集の画面の「一覧へ戻る」はページの左上（#618）。保存は Oracle への反映を伴うため、確認語欄の操作行に置く
        // （確認語が要る保存の例外。design-system README §4「詳細・作成・編集の画面の操作」）。
        back={
          activeView === "list"
            ? undefined
            : {
                label: t("profiles.action.backToList"),
                onClick: () => void backToList(),
                disabled: mutationBusy,
                testId: "profile-management-back",
              }
        }
      />

      <PageBody wide className="grid gap-4">
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
                loading={profileListLoading}
                loadingIndicator={!showProfileWorkspaceProcessing}
                search={profileSearch}
                sort={profileSort}
                onSearchChange={setProfileSearch}
                onSortChange={toggleSort}
                onSelect={selectProfile}
                profileHref={(profile) => appPath(`${location.pathname}?${new URLSearchParams({ profile: profile.id })}`)}
                hasNextPage={Boolean(profilesQuery.hasNextPage)}
                loadingNextPage={profilesQuery.isFetchingNextPage}
                loadMoreError={profileLoadMoreError}
                onLoadMore={() => void profilesQuery.fetchNextPage()}
                onRetryLoadMore={() => void profilesQuery.fetchNextPage()}
              />
            </DbObjectManagementPanelShell>
        ) : (
          <>
            <DbObjectManagementPanelShell
              id="profile-management-panel-editor"
              role="region"
              idPrefix="profile-management"
              ariaLabel={selectedProfile ? t("profiles.editor.edit") : t("profiles.editor.new")}
              processing={profileWorkspaceProcessing}
            >
              {selectedProfile || profileParam === "new" ? (
                editor
              ) : profileDetailQuery.isError ? (
                <ErrorState
                  message={profileDetailQuery.error instanceof Error ? profileDetailQuery.error.message : t("profiles.error.load")}
                  onRetry={() => void profileDetailQuery.refetch()}
                />
              ) : (
                <TimedLoadingState
                  label={t("profiles.detail.loading")}
                  operationKey="profile-detail-load"
                  framed={false}
                  testId="profile-editor-loading"
                >
                  <FormSkeleton fields={5} testId="profile-editor-skeleton" />
                </TimedLoadingState>
              )}
            </DbObjectManagementPanelShell>
          </>
        )}
      </PageBody>
    </>
  );
}
