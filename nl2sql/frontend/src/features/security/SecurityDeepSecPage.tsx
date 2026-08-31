import { BulkSelectionActions } from "@/components/BulkSelectionActions";
import { Button } from "@/components/ui/button";
import { DisclosureChevron } from "@/components/ui/disclosure-chevron";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  CheckCircle2,
  Clock3,
  Database,
  KeyRound,
  Play,
  Plus,
  RefreshCw,
  Save,
  Settings2,
  ShieldCheck,
  Trash2,
} from "lucide-react";

import {
  Banner,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  FormStatus,
  toast,
} from "@engchina/production-ready-ui";

import { PageHeader, PageHeaderStatusBadge } from "@/components/PageHeader";
import { StatusBadge } from "@/components/ui/status-badge";
import { DbObjectSearchOwnerFields } from "@/components/DbObjectFilterFields";
import { ProcessingIndicator } from "@/components/ProcessingState";
import { ErrorState } from "@/components/StateViews";
import { PageNotice } from "@/components/page-notice";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { FieldLabel, FieldLegend, RequiredIndicator } from "@/components/ui/required-field";
import {
  ExecutionConfirmationField,
  ManagementPanelHeader,
  ManagementPanelShell,
  ManagementTabs,
  WorkSection,
} from "@/features/nl2sql/components/DbAdminShared";
import { isAbortError } from "@/lib/api";
import { formatDateTimeWithYear } from "@/lib/format";
import { t } from "@/lib/i18n";
import { useRequestScope } from "@/lib/useRequestScope";
import { cn } from "@/lib/utils";
import { selectedVisibleKey } from "@/lib/visible-selection";
import { useAuth } from "./AuthProvider";
import { MENU_PERMISSIONS } from "./menu-permissions";
import { SecuritySearchField } from "./SecurityManagementShared";
import { securityApi } from "./api";
import type {
  DataEntitlement,
  DataEntitlementScopeFilter,
  DataEntitlementScopeOperator,
  DataEntitlementScopeValueSource,
  DataEntitlementScopeValueType,
  DeepSecDataEntitlementPreview,
  DeepSecPlan,
  DeepSecRoleEntitlements,
  DeepSecStatus,
  DeepSecStep,
  DeepSecTargetColumn,
  DeepSecTargetObject,
  DeepSecTargetObjectDetail,
  DeepSecVerification,
} from "./types";

const ENTITLEMENT_CAPABILITIES = ["SELECT"] as const;
const SCOPE_MODES = ["ALL", "FILTERS"] as const;
const NULL_SCOPE_OPERATORS = ["IS_NULL", "IS_NOT_NULL"] as const;
const LITERAL_SCOPE_VALUE_SOURCE = "LITERAL";
const LOGIN_USER_ID_SCOPE_VALUE_SOURCE = "LOGIN_USER_ID";
const LEGACY_APP_USER_ID_SCOPE_VALUE_SOURCE = "APP_USER_ID";
const SCOPE_OPERATORS_BY_VALUE_TYPE: Record<DataEntitlementScopeValueType, DataEntitlementScopeOperator[]> = {
  TEXT: ["EQ", "NE", "CONTAINS", "STARTS_WITH", "IN", "IS_NULL", "IS_NOT_NULL"],
  NUMBER: ["EQ", "NE", "GT", "GTE", "LT", "LTE", "BETWEEN", "IN", "IS_NULL", "IS_NOT_NULL"],
  TEMPORAL: ["EQ", "BEFORE", "ON_OR_BEFORE", "AFTER", "ON_OR_AFTER", "BETWEEN", "IS_NULL", "IS_NOT_NULL"],
};
type DeepSecView = "data-user" | "foundation" | "data-permissions";
type DataEntitlementDraft = DataEntitlement & { client_key: string };
type ScrollPositionSnapshot = {
  element: HTMLElement;
  top: number;
  left: number;
};

const INPUT_CLASS =
  "h-11 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20 disabled:bg-muted/20 disabled:text-muted";
const COMPACT_INPUT_CLASS =
  "h-9 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20 disabled:bg-muted/20 disabled:text-muted";
const ADMIN_EXECUTE_CONFIRMATION = "ADMIN_EXECUTE";
const ADMIN_RESET_CONFIRMATION = "ADMIN_RESET";
const TARGET_OBJECT_PAGE_SIZE = 50;

function loadErrorMessage(cause: unknown) {
  return cause instanceof Error && cause.message.trim()
    ? cause.message
    : t("security.common.loadError");
}

function stepStatus(step: DeepSecStep) {
  if (step.status === "APPLIED") return { variant: "success" as const, label: t("security.deepsec.complete") };
  if (step.status === "FAILED") return { variant: "danger" as const, label: t("security.deepsec.failed") };
  if (step.status === "RUNNING") return { variant: "pending" as const, label: t("security.deepsec.running") };
  return { variant: "neutral" as const, label: t("security.deepsec.pending") };
}

function normalizeScopeFilterValueSource(value: string | undefined): DataEntitlementScopeValueSource {
  const normalized = value?.trim().toUpperCase();
  return normalized === LOGIN_USER_ID_SCOPE_VALUE_SOURCE ||
    normalized === LEGACY_APP_USER_ID_SCOPE_VALUE_SOURCE
    ? LOGIN_USER_ID_SCOPE_VALUE_SOURCE
    : LITERAL_SCOPE_VALUE_SOURCE;
}

function scopeFilterSupportsValueSource(operator: string, valueType: string) {
  return operator === "EQ" && ["TEXT", "NUMBER"].includes(valueType);
}

function isPositiveIntegerScopeValue(value: string | undefined) {
  return /^[1-9]\d*$/.test((value ?? "").trim());
}

function migrateLegacyColumnEqualsEntitlement(item: DataEntitlement): DataEntitlement {
  const scopeMode = (item.scope_mode ?? "ALL").trim().toUpperCase();
  const scopeColumn = (item.scope_column ?? "").trim().toUpperCase();
  const scopeCode = (item.scope_code ?? "").trim();
  if (scopeMode !== "COLUMN_EQUALS") {
    return {
      ...item,
      scope_filters: (item.scope_filters ?? []).map((filter) => ({
        ...filter,
        value_source: normalizeScopeFilterValueSource(filter.value_source),
      })),
    };
  }
  return {
    ...item,
    scope_code: "FILTERS",
    scope_mode: "FILTERS",
    scope_column: "",
    scope_filters:
      scopeColumn && scopeCode && scopeCode !== "*"
        ? [
            {
              column_name: scopeColumn,
              operator: "EQ",
              value_type: "TEXT",
              value_source: LITERAL_SCOPE_VALUE_SOURCE,
              value: scopeCode,
              value_to: "",
              values: [],
            },
          ]
        : [],
  };
}

function entitlementDraftClientKey(item: DataEntitlement, index: number) {
  const entitlementId = item.entitlement_id?.trim();
  if (entitlementId) return `saved:${entitlementId}`;
  const targetKey =
    item.target_owner && item.target_object
      ? `${item.target_owner}.${item.target_object}`
      : item.resource_code || "blank";
  return `draft:${index}:${targetKey.toUpperCase()}`;
}

function toEntitlementDraft(item: DataEntitlement, index: number): DataEntitlementDraft {
  const migrated = migrateLegacyColumnEqualsEntitlement({ ...item });
  return {
    ...migrated,
    client_key: entitlementDraftClientKey(migrated, index),
  };
}

function blankEntitlementDraft(clientKey: string): DataEntitlementDraft {
  return {
    client_key: clientKey,
    resource_code: "",
    scope_code: "*",
    capability: "SELECT",
    target_owner: "",
    target_object: "",
    target_type: "TABLE",
    column_names: [],
    scope_mode: "ALL",
    scope_column: "",
    scope_filters: [],
  };
}

function entitlementDraft(role: DeepSecRoleEntitlements | null): DataEntitlementDraft[] {
  return role?.data_entitlements.map((item, index) => toEntitlementDraft(item, index)) ?? [];
}

function entitlementColumnsSummary(
  entitlement: DataEntitlement,
  detail: DeepSecTargetObjectDetail | undefined
) {
  const selectedCount = entitlement.column_names?.length ?? 0;
  if (!detail) {
    return t("security.deepsec.entitlements.columnsCount", { count: selectedCount });
  }
  return t("security.deepsec.entitlements.columnsSummary", {
    selected: selectedCount,
    total: detail.columns.length,
  });
}

function entitlementScopeSummary(entitlement: DataEntitlement) {
  if ((entitlement.scope_mode ?? "ALL") === "FILTERS") {
    return t("security.deepsec.entitlements.scopeFilterCount", {
      count: entitlement.scope_filters?.length ?? 0,
    });
  }
  return t("security.deepsec.entitlements.scopeAll");
}

function entitlementRoleStatus(role: DeepSecRoleEntitlements) {
  if (role.archived) return { variant: "neutral" as const, label: t("security.deepsec.entitlements.archived") };
  if (role.is_built_in) return { variant: "info" as const, label: t("security.deepsec.entitlements.builtIn") };
  return { variant: "success" as const, label: t("security.deepsec.entitlements.editable") };
}

function entitlementRoleSearchText(role: DeepSecRoleEntitlements) {
  return [
    role.role_code,
    role.display_name,
    role.description,
    ...role.data_entitlements.flatMap((item) => [
      item.resource_code,
      item.scope_code,
      item.capability,
      item.target_owner ?? "",
      item.target_object ?? "",
      item.scope_column ?? "",
      ...(item.scope_filters ?? []).flatMap((filter) => [
        filter.column_name,
        filter.operator,
        filter.value_type,
        filter.value_source ?? "",
        filter.value ?? "",
        filter.value_to ?? "",
        ...(filter.values ?? []),
      ]),
    ]),
  ]
    .join(" ")
    .toLowerCase();
}

function targetQualifiedName(item: Pick<DeepSecTargetObject, "name" | "owner" | "qualified_name">) {
  const qualifiedName = (item.qualified_name ?? "").trim();
  if (qualifiedName) return qualifiedName.toUpperCase();
  return `${item.owner}.${item.name}`.toUpperCase();
}

function mergeTargetObjectPages(
  current: DeepSecTargetObject[],
  next: DeepSecTargetObject[]
) {
  const merged = new Map<string, DeepSecTargetObject>();
  for (const item of [...current, ...next]) {
    merged.set(targetQualifiedName(item), item);
  }
  return Array.from(merged.values());
}

function targetObjectTypeLabel(objectType: string) {
  return objectType.replaceAll("_", " ").toUpperCase();
}

function entitlementTargetKey(entitlement: DataEntitlement) {
  if (entitlement.target_owner && entitlement.target_object) {
    return `${entitlement.target_owner}.${entitlement.target_object}`.toUpperCase();
  }
  return entitlement.resource_code.toUpperCase();
}

function entitlementApplyStatus(entitlement: DataEntitlement) {
  if (entitlement.apply_status === "APPLIED") {
    return { variant: "success" as const, label: t("security.deepsec.entitlements.applied") };
  }
  if (entitlement.apply_status === "FAILED") {
    return { variant: "danger" as const, label: t("security.deepsec.entitlements.failed") };
  }
  if (entitlement.apply_status === "RUNNING") {
    return { variant: "pending" as const, label: t("security.deepsec.entitlements.running") };
  }
  return { variant: "neutral" as const, label: t("security.deepsec.entitlements.pending") };
}

function oracleBaseType(dataType: string) {
  const normalized = dataType.trim().toUpperCase();
  if (normalized.startsWith("TIMESTAMP")) return "TIMESTAMP";
  return normalized.split("(", 1)[0].split(" ", 1)[0];
}

function scopeValueType(dataType: string): DataEntitlementScopeValueType | null {
  const baseType = oracleBaseType(dataType);
  if (["CHAR", "NCHAR", "VARCHAR2", "NVARCHAR2"].includes(baseType)) return "TEXT";
  if (["NUMBER", "FLOAT", "BINARY_FLOAT", "BINARY_DOUBLE"].includes(baseType)) return "NUMBER";
  if (["DATE", "TIMESTAMP"].includes(baseType)) return "TEMPORAL";
  return null;
}

function isSupportedScopeColumn(column: DeepSecTargetColumn) {
  return scopeValueType(column.data_type) !== null;
}

function defaultScopeOperator(valueType: DataEntitlementScopeValueType): DataEntitlementScopeOperator {
  return SCOPE_OPERATORS_BY_VALUE_TYPE[valueType][0];
}

function blankScopeFilter(columns: DeepSecTargetColumn[] = []): DataEntitlementScopeFilter {
  const column = columns.find(isSupportedScopeColumn);
  const valueType = column ? scopeValueType(column.data_type) ?? "TEXT" : "TEXT";
  return {
    column_name: column?.column_name ?? "",
    operator: defaultScopeOperator(valueType),
    value_type: valueType,
    value_source: LITERAL_SCOPE_VALUE_SOURCE,
    value: "",
    value_to: "",
    values: [],
  };
}

function normalizeScopeFilters(filters: DataEntitlementScopeFilter[] = []) {
  return filters
    .map((filter) => {
      const operator = filter.operator.trim().toUpperCase();
      const valueType = filter.value_type.trim().toUpperCase();
      const valueSource = scopeFilterSupportsValueSource(operator, valueType)
        ? normalizeScopeFilterValueSource(filter.value_source)
        : LITERAL_SCOPE_VALUE_SOURCE;
      return {
        column_name: filter.column_name.trim().toUpperCase(),
        operator,
        value_type: valueType,
        value_source: valueSource,
        value: valueSource === LOGIN_USER_ID_SCOPE_VALUE_SOURCE ? "" : (filter.value ?? "").trim(),
        value_to:
          valueSource === LOGIN_USER_ID_SCOPE_VALUE_SOURCE ? "" : (filter.value_to ?? "").trim(),
        values:
          valueSource === LOGIN_USER_ID_SCOPE_VALUE_SOURCE
            ? []
            : Array.from(
                new Set((filter.values ?? []).map((value) => value.trim()).filter(Boolean))
              ),
      };
    })
    .filter((filter) => filter.column_name);
}

function scopeFilterNeedsValue(operator: string) {
  return !NULL_SCOPE_OPERATORS.includes(operator as (typeof NULL_SCOPE_OPERATORS)[number]);
}

function scopeFilterNeedsValueTo(operator: string) {
  return operator === "BETWEEN";
}

function scopeFilterNeedsValues(operator: string) {
  return operator === "IN";
}

function normalizeEntitlementRows(rows: DataEntitlement[]) {
  return rows
    .map((item) => {
      const rawScopeMode = (item.scope_mode ?? "ALL").trim().toUpperCase();
      const legacyScopeColumn = (item.scope_column ?? "").trim().toUpperCase();
      const legacyScopeValue = item.scope_code.trim();
      const legacyFilters =
        rawScopeMode === "COLUMN_EQUALS" && legacyScopeColumn && legacyScopeValue && legacyScopeValue !== "*"
          ? [
              {
                column_name: legacyScopeColumn,
                operator: "EQ",
                value_type: "TEXT",
                value_source: LITERAL_SCOPE_VALUE_SOURCE,
                value: legacyScopeValue,
                value_to: "",
                values: [],
              },
            ]
          : item.scope_filters ?? [];
      const scopeMode = rawScopeMode === "COLUMN_EQUALS" ? "FILTERS" : rawScopeMode;
      return {
        entitlement_id: item.entitlement_id ?? "",
        resource_code: item.resource_code.trim().toUpperCase(),
        scope_code: item.scope_code.trim(),
        capability: "SELECT",
        target_owner: (item.target_owner ?? "").trim().toUpperCase(),
        target_object: (item.target_object ?? "").trim().toUpperCase(),
        target_type: (item.target_type ?? "TABLE").trim().toUpperCase(),
        column_names: Array.from(
          new Set((item.column_names ?? []).map((column) => column.trim().toUpperCase()).filter(Boolean))
        ),
        scope_mode: scopeMode,
        scope_column: "",
        scope_filters: scopeMode === "FILTERS" ? normalizeScopeFilters(legacyFilters) : [],
      };
    })
    .map((item) => ({
      ...item,
      scope_code: item.scope_mode === "ALL" ? "*" : "FILTERS",
    }));
}

function DeepSecTargetObjectPicker({
  index,
  value,
  selectedObject,
  objects,
  total,
  nextCursor,
  search,
  ownerPrefix,
  loading,
  loadingMore,
  error,
  disabled,
  onSearchChange,
  onOwnerPrefixChange,
  onSelect,
  onLoadMore,
}: {
  index: number;
  value: string;
  selectedObject: DeepSecTargetObject | null;
  objects: DeepSecTargetObject[];
  total: number | null;
  nextCursor: string | null;
  search: string;
  ownerPrefix: string;
  loading: boolean;
  loadingMore: boolean;
  error: string;
  disabled: boolean;
  onSearchChange: (value: string) => void;
  onOwnerPrefixChange: (value: string) => void;
  onSelect: (value: string) => void;
  onLoadMore: () => void;
}) {
  const titleId = `deepsec-entitlement-resource-${index}`;
  const loadedLabel =
    typeof total === "number"
      ? t("security.deepsec.entitlements.objectCount", {
          loaded: objects.length,
          total,
        })
      : t("security.deepsec.entitlements.objectLoadedCount", {
          loaded: objects.length,
        });
  const hasObjectFilter = Boolean(search.trim() || ownerPrefix.trim());
  return (
    <div className="grid gap-1 text-xs font-medium" data-testid={`security-deepsec-object-picker-${index}`}>
      <span id={titleId}>
        {t("security.deepsec.entitlements.resource")}
        <RequiredIndicator />
      </span>
      <div
        className="grid gap-2 rounded-md border border-border bg-background p-2"
        role="group"
        aria-labelledby={titleId}
      >
        <DbObjectSearchOwnerFields
          searchLabel={t("dbAdmin.search.label")}
          searchPlaceholder={t("dbAdmin.search.placeholder")}
          searchValue={search}
          onSearchChange={onSearchChange}
          ownerLabel={t("dbAdmin.owner.label")}
          ownerPlaceholder={t("dbAdmin.ownerPrefix.placeholder")}
          ownerValue={ownerPrefix}
          onOwnerChange={onOwnerPrefixChange}
          disabled={disabled}
        />
        <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
          <span className="min-w-0 break-all font-mono text-[11px] text-muted">
            {selectedObject || value
              ? t("security.deepsec.entitlements.objectSelected", {
                  object: selectedObject ? targetQualifiedName(selectedObject) : value,
                })
              : t("security.deepsec.entitlements.objectPlaceholder")}
          </span>
          <StatusBadge variant="info" label={loadedLabel} />
        </div>
        {error ? (
          <FormStatus
            tone="danger"
            message={t("security.deepsec.entitlements.objectLoadMoreError", {
              message: error,
            })}
          />
        ) : null}
        <div
          className="grid max-h-52 gap-1 overflow-auto rounded-md border border-border bg-card/30 p-1"
          role="listbox"
          aria-labelledby={titleId}
          data-entitlement-scroll-container
          data-testid={`security-deepsec-object-picker-list-${index}`}
        >
          {loading && objects.length === 0 ? (
            <p className="px-2 py-3 text-sm text-muted" role="status">
              {t("security.deepsec.entitlements.objectsLoading")}
            </p>
          ) : null}
          {!loading && objects.length === 0 ? (
            <div className="grid gap-1 px-2 py-3 text-sm text-muted">
              <p>
                {t(
                  hasObjectFilter
                    ? "security.deepsec.entitlements.objectEmpty"
                    : "security.deepsec.entitlements.objectEmptyUnfiltered"
                )}
              </p>
              {!hasObjectFilter ? (
                <p className="text-[11px] leading-5">
                  {t("security.deepsec.entitlements.objectEmptyHint")}
                </p>
              ) : null}
            </div>
          ) : null}
          {objects.map((object) => {
            const qualifiedName = targetQualifiedName(object);
            const selected = qualifiedName === value;
            return (
              <button
                key={qualifiedName}
                type="button"
                role="option"
                aria-selected={selected}
                className={cn(
                  "grid min-h-11 min-w-0 gap-1 rounded-md px-2 py-1.5 text-left outline-none transition hover:bg-muted/20 focus-visible:ring-2 focus-visible:ring-ring/40",
                  selected && "bg-primary/10 text-primary"
                )}
                disabled={disabled}
                onClick={() => onSelect(qualifiedName)}
              >
                <span className="break-all font-mono text-xs font-semibold">
                  {qualifiedName}
                </span>
                <span className="text-[11px] text-muted">
                  {targetObjectTypeLabel(object.object_type)}
                  {object.comment ? ` · ${object.comment}` : ""}
                </span>
              </button>
            );
          })}
        </div>
        <div className="grid min-w-0 gap-2">
          <p className="min-w-0 text-[11px] leading-5 text-muted">
            {t("security.deepsec.entitlements.oracleHelper")}
          </p>
          {nextCursor ? (
            <div className="flex min-w-0 justify-end">
              <Button
                type="button"
                variant="secondary"
                size="sm"
                className="w-full min-w-0 justify-center lg:w-auto"
                loading={loadingMore}
                disabled={disabled || loadingMore}
                data-testid={`security-deepsec-object-picker-load-more-${index}`}
                onClick={onLoadMore}
              >
                {t("security.deepsec.entitlements.objectLoadMore")}
              </Button>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function DeepSecPlanSteps({
  plan,
  stepNumbers,
  loading,
  loadError,
  onRetry,
}: {
  plan: DeepSecPlan | null;
  stepNumbers: readonly number[];
  loading: boolean;
  loadError: string;
  onRetry: () => void;
}) {
  if (loadError) {
    return <ErrorState message={loadError} onRetry={onRetry} />;
  }

  if (loading && !plan) {
    return (
      <Card>
        <CardContent>
          <p className="text-sm text-muted" role="status">{t("security.deepsec.planLoading")}</p>
        </CardContent>
      </Card>
    );
  }

  const steps = plan?.steps.filter((step) => stepNumbers.includes(step.step_no)) ?? [];
  if (plan && steps.length === 0) {
    return (
      <Card>
        <CardContent>
          <p className="text-sm text-muted">{t("security.deepsec.planEmpty")}</p>
        </CardContent>
      </Card>
    );
  }

  return steps.map((step) => {
    const statusBadge = stepStatus(step);
    const versionLabel = `${plan?.version ?? "V001"}.${step.step_no}`;
    return (
      <Card key={step.step_no} data-testid={`security-deepsec-step-${step.step_no}`}>
        <CardHeader className="gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0 flex-1">
            <CardTitle
              className="flex min-w-0 flex-wrap items-center gap-2"
              aria-label={`${versionLabel} ${step.title}`}
            >
              <span className="inline-flex shrink-0 items-center rounded-md border border-border bg-muted/30 px-2 py-0.5 font-mono text-xs font-semibold leading-5 tabular-nums text-muted">
                {versionLabel}
              </span>
              <span className="min-w-0 break-words">{step.title}</span>
            </CardTitle>
            <p className="mt-1 text-sm leading-6 text-muted">{step.description}</p>
          </div>
          <div className="flex shrink-0 flex-col items-start gap-2 sm:items-end">
            <StatusBadge variant={statusBadge.variant} label={statusBadge.label} />
            {step.status === "APPLIED" && step.executed_at ? (
              <span className="inline-flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-muted">
                <Clock3 size={14} className="shrink-0" aria-hidden />
                <span>{t("security.deepsec.appliedAt")}</span>
                <time
                  className="whitespace-nowrap font-mono tabular-nums text-foreground"
                  dateTime={step.executed_at}
                >
                  {formatDateTimeWithYear(step.executed_at)}
                </time>
              </span>
            ) : null}
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {step.error_message ? <Banner severity="danger">{step.error_message}</Banner> : null}
          <details className="group/disclosure min-w-0 rounded-md border border-border bg-background">
            <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 px-3 py-2 text-sm font-semibold outline-none focus-visible:ring-2 focus-visible:ring-ring/40 [&::-webkit-details-marker]:hidden">
              <span>{t("security.deepsec.sqlDetails")}</span>
              <DisclosureChevron
                expanded="group"
                size={16}
                className="text-muted"
              />
            </summary>
            <div className="space-y-4 border-t border-border p-3">
              <p className="text-sm leading-6 text-muted">{t("security.deepsec.sqlReadonly")}</p>
              <div className="space-y-1">
                <p className="text-xs font-medium text-muted">{t("security.deepsec.checksum")}</p>
                <code className="block break-all rounded-md bg-card p-2 text-[11px]">{step.checksum}</code>
              </div>
              <div className="space-y-3">
                {step.sql.map((sql, index) => (
                  <pre
                    key={`${step.step_no}-${index}`}
                    className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border bg-code p-3 text-code-fg text-xs leading-5"
                    tabIndex={0}
                    aria-label={`${step.title} SQL ${index + 1}`}
                  >
                    {sql}
                  </pre>
                ))}
              </div>
            </div>
          </details>
        </CardContent>
      </Card>
    );
  });
}

export function SecurityDeepSecPage() {
  const confirm = useConfirm();
  const { hasPermission } = useAuth();
  const mayApply = hasPermission(MENU_PERMISSIONS.securityDeepSec);
  const mayVerify = hasPermission(MENU_PERMISSIONS.securityDeepSec);
  const mayManageEntitlements = hasPermission(MENU_PERMISSIONS.securityDeepSec);
  const [activeView, setActiveView] = useState<DeepSecView>("data-user");
  const [status, setStatus] = useState<DeepSecStatus | null>(null);
  const [plan, setPlan] = useState<DeepSecPlan | null>(null);
  const [verification, setVerification] = useState<DeepSecVerification | null>(null);
  const [entitlementRoles, setEntitlementRoles] = useState<DeepSecRoleEntitlements[]>([]);
  const [selectedEntitlementRoleId, setSelectedEntitlementRoleId] = useState<string | null>(null);
  const [entitlementDraftRows, setEntitlementDraftRows] = useState<DataEntitlementDraft[]>([]);
  const [selectedEntitlementDraftKey, setSelectedEntitlementDraftKey] = useState<string | null>(null);
  const [entitlementPreview, setEntitlementPreview] =
    useState<DeepSecDataEntitlementPreview | null>(null);
  const [entitlementSqlPreviewOpen, setEntitlementSqlPreviewOpen] = useState(false);
  const [entitlementSearch, setEntitlementSearch] = useState("");
  const [targetObjectSearch, setTargetObjectSearch] = useState("");
  const [targetObjectOwnerPrefix, setTargetObjectOwnerPrefix] = useState("");
  const [targetObjects, setTargetObjects] = useState<DeepSecTargetObject[]>([]);
  const [targetObjectNextCursor, setTargetObjectNextCursor] = useState<string | null>(null);
  const [targetObjectTotal, setTargetObjectTotal] = useState<number | null>(null);
  const [targetDetails, setTargetDetails] = useState<Record<string, DeepSecTargetObjectDetail>>({});
  const [targetDetailLoading, setTargetDetailLoading] = useState<Record<string, boolean>>({});
  const [targetDetailErrors, setTargetDetailErrors] = useState<Record<string, string>>({});
  const [statusLoading, setStatusLoading] = useState(true);
  const [planLoading, setPlanLoading] = useState(true);
  const [entitlementLoading, setEntitlementLoading] = useState(true);
  const [targetObjectsLoading, setTargetObjectsLoading] = useState(true);
  const [targetObjectsLoadingMore, setTargetObjectsLoadingMore] = useState(false);
  const [foundationApplying, setFoundationApplying] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [entitlementPreviewing, setEntitlementPreviewing] = useState(false);
  const [entitlementApplying, setEntitlementApplying] = useState(false);
  const [dataUserPassword, setDataUserPassword] = useState("");
  const [configSaving, setConfigSaving] = useState(false);
  const [configSyncing, setConfigSyncing] = useState(false);
  const [configError, setConfigError] = useState("");
  const [statusLoadError, setStatusLoadError] = useState("");
  const [planLoadError, setPlanLoadError] = useState("");
  const [entitlementLoadError, setEntitlementLoadError] = useState("");
  const [targetObjectLoadError, setTargetObjectLoadError] = useState("");
  const [entitlementFormError, setEntitlementFormError] = useState("");
  const [entitlementApplyConfirmation, setEntitlementApplyConfirmation] = useState("");
  const [actionError, setActionError] = useState("");
  const [foundationApplyError, setFoundationApplyError] = useState("");
  const [resetError, setResetError] = useState("");
  const [foundationApplyPanelOpen, setFoundationApplyPanelOpen] = useState(true);
  const [foundationApplyConfirmation, setFoundationApplyConfirmation] = useState("");
  const [resetPanelOpen, setResetPanelOpen] = useState(false);
  const [resetConfirmation, setResetConfirmation] = useState("");
  const statusLoadSequence = useRef(0);
  const planLoadSequence = useRef(0);
  const entitlementLoadSequence = useRef(0);
  const targetObjectsLoadSequence = useRef(0);
  const entitlementDraftKeySequence = useRef(0);
  const entitlementEditorScrollRef = useRef<HTMLDivElement | null>(null);
  const pendingEntitlementScrollPositionsRef = useRef<ScrollPositionSnapshot[]>([]);
  const targetObjectFilterMounted = useRef(false);
  const { abortAll: abortStatusRequests, run: runStatusRequest } = useRequestScope();
  const { abortAll: abortPlanRequests, run: runPlanRequest } = useRequestScope();
  const { abortAll: abortEntitlementRequests, run: runEntitlementRequest } = useRequestScope();
  const { abortAll: abortTargetObjectRequests, run: runTargetObjectRequest } = useRequestScope();
  const refreshing =
    statusLoading ||
    planLoading ||
    entitlementLoading ||
    targetObjectsLoading ||
    targetObjectsLoadingMore;

  const filteredEntitlementRoles = useMemo(() => {
    const q = entitlementSearch.trim().toLowerCase();
    return entitlementRoles
      .filter((role) => (q ? entitlementRoleSearchText(role).includes(q) : true))
      .sort((left, right) => left.display_name.localeCompare(right.display_name, "ja"));
  }, [entitlementRoles, entitlementSearch]);
  const visibleSelectedEntitlementRoleId =
    activeView === "data-permissions"
      ? selectedVisibleKey(filteredEntitlementRoles, selectedEntitlementRoleId, (role) => role.role_id)
      : selectedEntitlementRoleId;
  const selectedEntitlementRole = useMemo(
    () => entitlementRoles.find((role) => role.role_id === visibleSelectedEntitlementRoleId) ?? null,
    [entitlementRoles, visibleSelectedEntitlementRoleId]
  );
  const targetObjectMap = useMemo(() => {
    return new Map(targetObjects.map((item) => [targetQualifiedName(item), item]));
  }, [targetObjects]);
  const visibleTargetObjects = useMemo(
    () =>
      targetObjects.filter((item) => {
        const name = item.name.toUpperCase();
        const owner = item.owner.toUpperCase();
        return !(
          owner === "SYS" ||
          owner === "SYSTEM" ||
          name.startsWith("NL2SQL_APP_") ||
          name.startsWith("NL2SQL_AUTH_") ||
          name.startsWith("NL2SQL_DEEPSEC_")
        );
      }),
    [targetObjects]
  );
  const entitlementReadOnly = Boolean(
    !mayManageEntitlements || selectedEntitlementRole?.is_built_in || selectedEntitlementRole?.archived
  );
  const normalizedEntitlementDraftRows = useMemo(
    () => normalizeEntitlementRows(entitlementDraftRows),
    [entitlementDraftRows]
  );
  const savedEntitlementRows = useMemo(
    () => normalizeEntitlementRows(selectedEntitlementRole?.data_entitlements ?? []),
    [selectedEntitlementRole]
  );
  const entitlementDraftChanged =
    JSON.stringify(normalizedEntitlementDraftRows) !== JSON.stringify(savedEntitlementRows);
  const entitlementApplyConfirmed =
    entitlementApplyConfirmation.trim() === ADMIN_EXECUTE_CONFIRMATION;
  const selectedRolePreviewRows = entitlementPreview?.data_entitlements ?? [];
  const selectedRoleCleanupSql = entitlementPreview?.cleanup_sql ?? [];
  const selectedRolePreviewSqlCount =
    selectedRoleCleanupSql.length +
    selectedRolePreviewRows.reduce((count, item) => count + (item.sql?.length ?? 0), 0);
  const selectedDraftTargetKeys = useMemo(
    () => Array.from(new Set(entitlementDraftRows.map(entitlementTargetKey).filter(Boolean))),
    [entitlementDraftRows]
  );
  const visibleSelectedEntitlementDraftKey =
    selectedEntitlementDraftKey &&
    entitlementDraftRows.some((item) => item.client_key === selectedEntitlementDraftKey)
      ? selectedEntitlementDraftKey
      : entitlementDraftRows[0]?.client_key ?? null;
  const selectedEntitlementDraftIndex = visibleSelectedEntitlementDraftKey
    ? entitlementDraftRows.findIndex((item) => item.client_key === visibleSelectedEntitlementDraftKey)
    : -1;
  const selectedEntitlementDraft =
    selectedEntitlementDraftIndex >= 0 ? entitlementDraftRows[selectedEntitlementDraftIndex] : null;
  useLayoutEffect(() => {
    const snapshots = pendingEntitlementScrollPositionsRef.current;
    if (snapshots.length === 0) return;
    pendingEntitlementScrollPositionsRef.current = [];

    const restore = () => {
      for (const snapshot of snapshots) {
        if (!snapshot.element.isConnected) continue;
        snapshot.element.scrollTo({
          top: snapshot.top,
          left: snapshot.left,
          behavior: "auto",
        });
      }
    };

    restore();
    const frame = window.requestAnimationFrame(restore);
    return () => window.cancelAnimationFrame(frame);
  }, [entitlementDraftRows]);
  useEffect(() => {
    if (!visibleSelectedEntitlementDraftKey || typeof window === "undefined") return;
    const frame = window.requestAnimationFrame(() => {
      entitlementEditorScrollRef.current?.scrollTo({ top: 0, left: 0, behavior: "auto" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [visibleSelectedEntitlementDraftKey]);
  const foundationSteps = useMemo(
    () =>
      (plan?.steps.filter((step) => step.step_no === 1 || step.step_no === 2) ?? []).sort(
        (left, right) => left.step_no - right.step_no
      ),
    [plan]
  );
  const foundationReady = foundationSteps.length === 2 && foundationSteps.every((step) => step.status === "APPLIED");
  const foundationPendingSteps = foundationSteps.filter((step) => step.status !== "APPLIED");
  const foundationApplyVisible = Boolean(mayApply && foundationPendingSteps.length > 0);
  const foundationApplyConfirmed =
    foundationApplyConfirmation.trim() === ADMIN_EXECUTE_CONFIRMATION;
  const foundationApplyBlocked = Boolean(
    !plan?.deepsec_enabled ||
      !plan.has_data_user_password ||
      foundationPendingSteps.length === 0 ||
      foundationSteps.some((step) => step.status === "RUNNING")
  );
  const resetAvailable = Boolean(
    plan?.steps.some((step) => step.status === "APPLIED" || step.status === "FAILED")
  );
  const resetConfirmed = resetConfirmation.trim() === ADMIN_RESET_CONFIRMATION;
  const hasSavedDataUserPassword = Boolean(status?.has_data_user_password ?? plan?.has_data_user_password);
  const passwordSyncDisabled = Boolean(
    !hasSavedDataUserPassword || dataUserPassword || configSaving || configSyncing
  );

  const loadStatus = async () => {
    const sequence = statusLoadSequence.current + 1;
    let completed = false;
    statusLoadSequence.current = sequence;
    setStatusLoading(true);
    setStatusLoadError("");
    try {
      await runStatusRequest(async (signal) => {
        const nextStatus = await securityApi.deepSecStatus({ signal });
        if (signal.aborted || sequence !== statusLoadSequence.current) return;
        setStatus(nextStatus);
        completed = true;
      });
    } catch (cause) {
      if (isAbortError(cause)) {
        return false;
      }
      if (sequence === statusLoadSequence.current) {
        setStatusLoadError(loadErrorMessage(cause));
      }
    } finally {
      if (sequence === statusLoadSequence.current) setStatusLoading(false);
    }
    return completed;
  };

  const loadPlan = async () => {
    const sequence = planLoadSequence.current + 1;
    let completed = false;
    planLoadSequence.current = sequence;
    setPlanLoading(true);
    setPlanLoadError("");
    try {
      await runPlanRequest(async (signal) => {
        const nextPlan = await securityApi.deepSecPlan({ signal });
        if (signal.aborted || sequence !== planLoadSequence.current) return;
        setPlan(nextPlan);
        completed = true;
      });
    } catch (cause) {
      if (isAbortError(cause)) {
        return false;
      }
      if (sequence === planLoadSequence.current) {
        setPlanLoadError(loadErrorMessage(cause));
      }
    } finally {
      if (sequence === planLoadSequence.current) setPlanLoading(false);
    }
    return completed;
  };

  const loadEntitlements = async () => {
    const sequence = entitlementLoadSequence.current + 1;
    let completed = false;
    entitlementLoadSequence.current = sequence;
    setEntitlementLoading(true);
    setEntitlementLoadError("");
    try {
      await runEntitlementRequest(async (signal) => {
        const rows = await securityApi.deepSecDataEntitlements({ signal });
        if (signal.aborted || sequence !== entitlementLoadSequence.current) return;
        setEntitlementRoles(rows);
        setSelectedEntitlementRoleId((current) =>
          current && rows.some((role) => role.role_id === current)
            ? current
            : null
        );
        completed = true;
      });
    } catch (cause) {
      if (isAbortError(cause)) {
        return false;
      }
      if (sequence === entitlementLoadSequence.current) {
        setEntitlementLoadError(loadErrorMessage(cause));
      }
    } finally {
      if (sequence === entitlementLoadSequence.current) setEntitlementLoading(false);
    }
    return completed;
  };

  const loadTargetObjects = async ({
    cursor = null,
    append = false,
  }: {
    cursor?: string | null;
    append?: boolean;
  } = {}) => {
    const sequence = targetObjectsLoadSequence.current + 1;
    let completed = false;
    targetObjectsLoadSequence.current = sequence;
    if (append) {
      setTargetObjectsLoadingMore(true);
    } else {
      setTargetObjectsLoading(true);
    }
    setTargetObjectLoadError("");
    try {
      await runTargetObjectRequest(async (signal) => {
        const page = await securityApi.deepSecTargetObjects({
          signal,
          q: targetObjectSearch,
          ownerPrefix: targetObjectOwnerPrefix,
          cursor,
          limit: TARGET_OBJECT_PAGE_SIZE,
        });
        if (signal.aborted || sequence !== targetObjectsLoadSequence.current) return;
        setTargetObjects((current) =>
          append ? mergeTargetObjectPages(current, page.items) : page.items
        );
        setTargetObjectNextCursor(page.next_cursor ?? null);
        setTargetObjectTotal(typeof page.total === "number" ? page.total : null);
        completed = true;
      });
    } catch (cause) {
      if (isAbortError(cause)) {
        return false;
      }
      if (sequence === targetObjectsLoadSequence.current) {
        setTargetObjectLoadError(loadErrorMessage(cause));
      }
    } finally {
      if (sequence === targetObjectsLoadSequence.current) {
        if (append) {
          setTargetObjectsLoadingMore(false);
        } else {
          setTargetObjectsLoading(false);
        }
      }
    }
    return completed;
  };

  const loadTargetDetail = async (object: DeepSecTargetObject) => {
    const key = targetQualifiedName(object);
    if (targetDetails[key] || targetDetailLoading[key]) return;
    setTargetDetailLoading((current) => ({ ...current, [key]: true }));
    setTargetDetailErrors((current) => ({ ...current, [key]: "" }));
    try {
      const detail = await securityApi.deepSecTargetObjectDetail(object);
      setTargetDetails((current) => ({ ...current, [key]: detail }));
    } catch (cause) {
      setTargetDetailErrors((current) => ({ ...current, [key]: loadErrorMessage(cause) }));
    } finally {
      setTargetDetailLoading((current) => ({ ...current, [key]: false }));
    }
  };

  const load = async (announce = false) => {
    setActionError("");
    const results = await Promise.all([
      loadStatus(),
      loadPlan(),
      loadEntitlements(),
      loadTargetObjects(),
    ]);
    if (announce && results.every(Boolean)) {
      toast.success(t("common.action.refreshed"));
    }
  };

  useEffect(() => {
    void load();
    return () => {
      statusLoadSequence.current += 1;
      planLoadSequence.current += 1;
      entitlementLoadSequence.current += 1;
      targetObjectsLoadSequence.current += 1;
      abortStatusRequests();
      abortPlanRequests();
      abortEntitlementRequests();
      abortTargetObjectRequests();
    };
  }, []);

  useEffect(() => {
    if (activeView !== "data-permissions" || entitlementLoading) return;
    setSelectedEntitlementRoleId((current) =>
      selectedVisibleKey(filteredEntitlementRoles, current, (role) => role.role_id)
    );
  }, [activeView, entitlementLoading, filteredEntitlementRoles]);

  useEffect(() => {
    if (!targetObjectFilterMounted.current) {
      targetObjectFilterMounted.current = true;
      return;
    }
    const timeout = window.setTimeout(() => {
      void loadTargetObjects();
    }, 250);
    return () => window.clearTimeout(timeout);
  }, [targetObjectSearch, targetObjectOwnerPrefix]);

  useEffect(() => {
    const nextDraftRows = entitlementDraft(selectedEntitlementRole);
    setEntitlementDraftRows(nextDraftRows);
    setSelectedEntitlementDraftKey(nextDraftRows[0]?.client_key ?? null);
    setEntitlementPreview(null);
    setEntitlementSqlPreviewOpen(false);
    setEntitlementFormError("");
    setEntitlementApplyConfirmation("");
  }, [selectedEntitlementRole?.role_id, selectedEntitlementRole?.version]);

  useEffect(() => {
    for (const key of selectedDraftTargetKeys) {
      const draft = entitlementDraftRows.find((item) => entitlementTargetKey(item) === key);
      const object =
        targetObjectMap.get(key) ??
        (draft?.target_owner && draft?.target_object
          ? {
              owner: draft.target_owner,
              name: draft.target_object,
              qualified_name: key,
              object_type: draft.target_type ?? "TABLE",
              comment: "",
            }
          : null);
      if (object) void loadTargetDetail(object);
    }
  }, [selectedDraftTargetKeys.join("|"), targetObjectMap, entitlementDraftRows]);

  useEffect(() => {
    if (foundationApplyVisible) {
      setFoundationApplyPanelOpen(true);
      return;
    }
    setFoundationApplyPanelOpen(false);
    setFoundationApplyConfirmation("");
    setFoundationApplyError("");
  }, [foundationApplyVisible]);

  useEffect(() => {
    if (!resetAvailable) {
      setResetPanelOpen(false);
      setResetConfirmation("");
      setResetError("");
    }
  }, [resetAvailable]);

  const validateConfigForm = () => {
    if (dataUserPassword.length < 12 || dataUserPassword.length > 256) {
      return t("security.deepsec.config.passwordLength");
    }
    if (dataUserPassword.includes("\"") || /[\x00-\x1f\x7f-\x9f]/.test(dataUserPassword)) {
      return t("security.deepsec.config.passwordChars");
    }
    return "";
  };

  const handleSaveConfig = async () => {
    if (configSaving || configSyncing) return;
    const validationError = validateConfigForm();
    if (validationError) {
      setConfigError(validationError);
      return;
    }
    setConfigSaving(true);
    setConfigError("");
    setActionError("");
    try {
      const nextStatus = await securityApi.updateDeepSecConfig(dataUserPassword);
      setStatus(nextStatus);
      setDataUserPassword("");
      toast.success(t("security.deepsec.config.saved"));
      await loadPlan();
    } catch (cause) {
      setConfigError(cause instanceof Error ? cause.message : t("security.common.saveError"));
    } finally {
      setConfigSaving(false);
    }
  };

  const handleSyncConfig = async () => {
    if (configSaving || configSyncing) return;
    if (!hasSavedDataUserPassword) {
      setConfigError(t("security.deepsec.config.syncMissing"));
      return;
    }
    if (dataUserPassword) return;
    setConfigSyncing(true);
    setConfigError("");
    setActionError("");
    try {
      const nextStatus = await securityApi.syncDeepSecConfigPassword();
      setStatus(nextStatus);
      toast.success(t("security.deepsec.config.synced"));
      await loadPlan();
    } catch (cause) {
      setConfigError(cause instanceof Error ? cause.message : t("security.common.saveError"));
    } finally {
      setConfigSyncing(false);
    }
  };

  const handleApplyFoundation = async () => {
    if (!plan) return;
    const confirmation = foundationApplyConfirmation.trim();
    if (confirmation !== ADMIN_EXECUTE_CONFIRMATION) {
      setFoundationApplyError(t("security.deepsec.applyFoundationConfirmationRequired"));
      return;
    }
    const stepsToApply = foundationPendingSteps
      .filter((step) => step.status !== "RUNNING")
      .sort((left, right) => left.step_no - right.step_no);
    if (stepsToApply.length === 0) return;
    setFoundationApplying(true);
    setFoundationApplyError("");
    try {
      for (const step of stepsToApply) {
        await securityApi.applyDeepSecStep(plan.version, step, confirmation);
      }
      setFoundationApplyConfirmation("");
      setFoundationApplyPanelOpen(false);
      toast.success(t("security.deepsec.applyFoundationDone"));
      await Promise.all([loadStatus(), loadPlan(), loadEntitlements()]);
    } catch (cause) {
      setFoundationApplyError(cause instanceof Error ? cause.message : t("security.common.saveError"));
      await Promise.all([loadStatus(), loadPlan()]);
    } finally {
      setFoundationApplying(false);
    }
  };

  const handleReset = async () => {
    if (!plan) return;
    if (resetConfirmation.trim() !== ADMIN_RESET_CONFIRMATION) {
      setResetError(t("security.deepsec.resetConfirmationRequired"));
      return;
    }
    setResetting(true);
    setResetError("");
    try {
      await securityApi.resetDeepSecPlan(plan.version, resetConfirmation.trim());
      setResetConfirmation("");
      setResetPanelOpen(false);
      setVerification(null);
      toast.success(t("security.deepsec.resetDone"));
      await load();
    } catch (cause) {
      setResetError(cause instanceof Error ? cause.message : t("security.common.saveError"));
    } finally {
      setResetting(false);
    }
  };

  const handleVerify = async () => {
    if (
      !(await confirm({
        title: t("security.deepsec.verify"),
        description: t("security.deepsec.verifyConfirm"),
        tone: "info",
      }))
    ) {
      return;
    }
    setVerifying(true);
    setActionError("");
    try {
      const result = await securityApi.verifyDeepSec();
      setVerification(result);
      toast.success(t("security.deepsec.verified"));
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : t("security.common.saveError"));
    } finally {
      setVerifying(false);
    }
  };

  const addEntitlement = () => {
    entitlementDraftKeySequence.current += 1;
    const nextDraft = blankEntitlementDraft(
      `new:${selectedEntitlementRole?.role_id ?? "role"}:${entitlementDraftKeySequence.current}`
    );
    setEntitlementPreview(null);
    setEntitlementSqlPreviewOpen(false);
    setEntitlementDraftRows((current) => [...current, nextDraft]);
    setSelectedEntitlementDraftKey(nextDraft.client_key);
    setEntitlementFormError("");
  };

  const patchEntitlement = (index: number, patch: Partial<DataEntitlement>) => {
    setEntitlementPreview(null);
    setEntitlementSqlPreviewOpen(false);
    setEntitlementDraftRows((current) =>
      current.map((item, itemIndex) => (itemIndex === index ? { ...item, ...patch } : item))
    );
    setEntitlementFormError("");
  };

  const updateEntitlementTarget = (index: number, value: string) => {
    const object = targetObjectMap.get(value);
    if (!object) {
      patchEntitlement(index, {
        resource_code: value,
        target_owner: "",
        target_object: "",
        target_type: "TABLE",
        column_names: [],
        scope_mode: "ALL",
        scope_column: "",
        scope_filters: [],
        scope_code: "*",
      });
      return;
    }
    patchEntitlement(index, {
      resource_code: targetQualifiedName(object),
      target_owner: object.owner,
      target_object: object.name,
      target_type: object.object_type,
      column_names: [],
      scope_mode: "ALL",
      scope_column: "",
      scope_filters: [],
      scope_code: "*",
      capability: "SELECT",
    });
    void loadTargetDetail(object);
  };

  const toggleEntitlementColumn = (index: number, columnName: string, checked: boolean) => {
    setEntitlementPreview(null);
    setEntitlementDraftRows((current) =>
      current.map((item, itemIndex) => {
        if (itemIndex !== index) return item;
        const columns = new Set((item.column_names ?? []).map((column) => column.toUpperCase()));
        if (checked) {
          columns.add(columnName.toUpperCase());
        } else {
          columns.delete(columnName.toUpperCase());
        }
        return { ...item, column_names: Array.from(columns) };
      })
    );
    setEntitlementFormError("");
  };

  const setEntitlementColumns = (index: number, columnNames: string[]) => {
    patchEntitlement(index, {
      column_names: Array.from(
        new Set(columnNames.map((columnName) => columnName.trim().toUpperCase()).filter(Boolean))
      ),
    });
  };

  const setEntitlementColumnsPreservingScroll = (index: number, columnNames: string[]) => {
    const editor = entitlementEditorScrollRef.current;
    if (editor) {
      const main = editor.closest<HTMLElement>("main");
      const internalContainers = Array.from(
        editor.querySelectorAll<HTMLElement>("[data-entitlement-scroll-container]")
      );
      const seen = new Set<HTMLElement>();
      pendingEntitlementScrollPositionsRef.current = [main, editor, ...internalContainers]
        .filter((element): element is HTMLElement => {
          if (!element || seen.has(element)) return false;
          seen.add(element);
          return true;
        })
        .map((element) => {
          return {
            element,
            top: element.scrollTop,
            left: element.scrollLeft,
          };
        });
    }
    setEntitlementColumns(index, columnNames);
  };

  const addScopeFilter = (index: number, columns: DeepSecTargetColumn[]) => {
    setEntitlementPreview(null);
    setEntitlementDraftRows((current) =>
      current.map((item, itemIndex) =>
        itemIndex === index
          ? {
              ...item,
              scope_filters: [...(item.scope_filters ?? []), blankScopeFilter(columns)],
            }
          : item
      )
    );
    setEntitlementFormError("");
  };

  const removeScopeFilter = (index: number, filterIndex: number) => {
    setEntitlementPreview(null);
    setEntitlementDraftRows((current) =>
      current.map((item, itemIndex) =>
        itemIndex === index
          ? {
              ...item,
              scope_filters: (item.scope_filters ?? []).filter(
                (_filter, currentFilterIndex) => currentFilterIndex !== filterIndex
              ),
            }
          : item
      )
    );
    setEntitlementFormError("");
  };

  const patchScopeFilter = (
    index: number,
    filterIndex: number,
    patch: Partial<DataEntitlementScopeFilter>
  ) => {
    setEntitlementPreview(null);
    setEntitlementDraftRows((current) =>
      current.map((item, itemIndex) =>
        itemIndex === index
          ? {
              ...item,
              scope_filters: (item.scope_filters ?? []).map((filter, currentFilterIndex) =>
                currentFilterIndex === filterIndex ? { ...filter, ...patch } : filter
              ),
            }
          : item
      )
    );
    setEntitlementFormError("");
  };

  const updateScopeFilterColumn = (
    index: number,
    filterIndex: number,
    columnName: string,
    columns: DeepSecTargetColumn[]
  ) => {
    const column = columns.find(
      (item) => item.column_name.toUpperCase() === columnName.toUpperCase()
    );
    const valueType = column ? scopeValueType(column.data_type) ?? "TEXT" : "TEXT";
    patchScopeFilter(index, filterIndex, {
      column_name: columnName,
      value_type: valueType,
      operator: defaultScopeOperator(valueType),
      value_source: LITERAL_SCOPE_VALUE_SOURCE,
      value: "",
      value_to: "",
      values: [],
    });
  };

  const updateScopeFilterOperator = (
    index: number,
    filterIndex: number,
    operator: string,
    valueType: DataEntitlementScopeValueType
  ) => {
    patchScopeFilter(index, filterIndex, {
      operator,
      value: "",
      value_to: "",
      values: [],
      value_type: valueType,
      value_source: LITERAL_SCOPE_VALUE_SOURCE,
    });
  };

  const removeEntitlement = (index: number) => {
    const nextRows = entitlementDraftRows.filter((_, itemIndex) => itemIndex !== index);
    const removedKey = entitlementDraftRows[index]?.client_key ?? null;
    setEntitlementPreview(null);
    setEntitlementSqlPreviewOpen(false);
    setEntitlementDraftRows(nextRows);
    setSelectedEntitlementDraftKey((current) => {
      if (current && current !== removedKey && nextRows.some((item) => item.client_key === current)) {
        return current;
      }
      return nextRows[Math.min(index, nextRows.length - 1)]?.client_key ?? null;
    });
    setEntitlementFormError("");
  };

  const validateEntitlements = () => {
    for (const item of normalizedEntitlementDraftRows) {
      if (
        !item.resource_code ||
        !item.target_owner ||
        !item.target_object ||
        !item.scope_code ||
        item.column_names.length === 0 ||
        !ENTITLEMENT_CAPABILITIES.includes(
          item.capability as (typeof ENTITLEMENT_CAPABILITIES)[number]
        ) ||
        !SCOPE_MODES.includes(item.scope_mode as (typeof SCOPE_MODES)[number])
      ) {
        return t("security.deepsec.entitlements.validation");
      }
      if (item.scope_mode === "FILTERS") {
        if (!item.scope_filters.length) {
          return t("security.deepsec.entitlements.scopeFilterValidation");
        }
        for (const filter of item.scope_filters) {
          const valueSource = normalizeScopeFilterValueSource(filter.value_source);
          if (
            !filter.column_name ||
            !filter.operator ||
            !["TEXT", "NUMBER", "TEMPORAL"].includes(filter.value_type)
          ) {
            return t("security.deepsec.entitlements.scopeFilterValidation");
          }
          if (valueSource === LOGIN_USER_ID_SCOPE_VALUE_SOURCE) {
            if (!scopeFilterSupportsValueSource(filter.operator, filter.value_type)) {
              return t("security.deepsec.entitlements.scopeFilterValidation");
            }
            continue;
          }
          if (scopeFilterNeedsValues(filter.operator) && !filter.values?.length) {
            return t("security.deepsec.entitlements.scopeFilterValidation");
          }
          if (
            scopeFilterNeedsValue(filter.operator) &&
            !scopeFilterNeedsValues(filter.operator) &&
            !filter.value
          ) {
            return t("security.deepsec.entitlements.scopeFilterValidation");
          }
          if (
            filter.value_type === "NUMBER" &&
            filter.operator === "EQ" &&
            !isPositiveIntegerScopeValue(filter.value)
          ) {
            return t("security.deepsec.entitlements.scopeFilterPositiveIntegerValidation");
          }
          if (scopeFilterNeedsValueTo(filter.operator) && !filter.value_to) {
            return t("security.deepsec.entitlements.scopeFilterValidation");
          }
        }
      }
    }
    return "";
  };

  const handlePreviewEntitlements = async () => {
    if (!selectedEntitlementRole || entitlementReadOnly) return;
    const validationError = validateEntitlements();
    if (validationError) {
      setEntitlementFormError(validationError);
      return;
    }
    setEntitlementPreviewing(true);
    setEntitlementFormError("");
    setActionError("");
    try {
      const preview = await securityApi.previewDeepSecDataEntitlements(
        {
          ...selectedEntitlementRole,
          data_entitlements: normalizedEntitlementDraftRows,
        }
      );
      setEntitlementPreview(preview);
      setEntitlementSqlPreviewOpen(true);
      setEntitlementDraftRows((current) =>
        preview.data_entitlements.map((item, index) => {
          const currentDraft = current[index];
          return {
            ...(currentDraft ?? {}),
            ...item,
            client_key: currentDraft?.client_key ?? entitlementDraftClientKey(item, index),
          };
        })
      );
      toast.success(t("security.deepsec.entitlements.previewed"));
    } catch (cause) {
      setEntitlementFormError(cause instanceof Error ? cause.message : t("security.common.loadError"));
    } finally {
      setEntitlementPreviewing(false);
    }
  };

  const handleApplyEntitlements = async () => {
    if (!selectedEntitlementRole || entitlementReadOnly) return;
    const validationError = validateEntitlements();
    if (validationError) {
      setEntitlementFormError(validationError);
      return;
    }
    if (entitlementApplyConfirmation.trim() !== ADMIN_EXECUTE_CONFIRMATION) {
      setEntitlementFormError(t("security.deepsec.entitlements.applyConfirmationRequired"));
      return;
    }
    setEntitlementApplying(true);
    setEntitlementFormError("");
    setActionError("");
    try {
      const result = await securityApi.applyDeepSecDataEntitlements(
        {
          ...selectedEntitlementRole,
          data_entitlements: normalizedEntitlementDraftRows,
        },
        entitlementApplyConfirmation.trim()
      );
      const updated = result.role;
      setEntitlementRoles((rows) =>
        rows.map((role) => (role.role_id === updated.role_id ? updated : role))
      );
      setEntitlementApplyConfirmation("");
      setEntitlementPreview(null);
      setVerification(null);
      toast.success(t("security.deepsec.entitlements.appliedToast"));
      await load();
    } catch (cause) {
      setEntitlementFormError(cause instanceof Error ? cause.message : t("security.common.saveError"));
    } finally {
      setEntitlementApplying(false);
    }
  };

  return (
    <>
      <PageHeader
        title={t("nav.securityDeepSec")}
        subtitle={t("security.deepsec.subtitle")}
        status={
          statusLoadError ? (
            <PageHeaderStatusBadge variant="danger" label={t("security.deepsec.statusLoadFailed")} />
          ) : statusLoading && !status ? (
            <PageHeaderStatusBadge variant="pending" label={t("security.deepsec.statusChecking")} />
          ) : (
            <PageHeaderStatusBadge
              variant={status?.configured ? "success" : "warning"}
              label={status?.configured ? t("security.deepsec.statusConfigured") : t("security.deepsec.statusUnconfigured")}
            />
          )
        }
        actions={[
          {
            id: "refresh",
            kind: "utility",
            label: t("common.action.refresh"),
            icon: RefreshCw,
            onClick: () => load(true),
            loading: refreshing,
          },
        ]}
      />
      <main className="grid gap-4 p-4 lg:p-8">
        <PageNotice
          notice={statusLoadError ? { tone: "danger", message: statusLoadError } : null}
          action={
            statusLoadError ? (
              <Button type="button" variant="secondary" size="sm" onClick={() => void loadStatus()}>
                <RefreshCw size={14} aria-hidden />
                {t("security.common.reload")}
              </Button>
            ) : null
          }
        />
        {plan && !plan.deepsec_enabled ? (
          <Banner severity="warning">{t("security.deepsec.banner.disabled")}</Banner>
        ) : null}
        {plan?.deepsec_enabled && !plan.has_data_user_password ? (
          <Banner severity="warning">{t("security.deepsec.banner.passwordMissing")}</Banner>
        ) : null}
        <ManagementTabs
          activeView={activeView}
          tabs={[
            { id: "data-user", label: t("security.deepsec.tabs.dataUser"), icon: KeyRound },
            { id: "foundation", label: t("security.deepsec.tabs.foundation"), icon: Settings2 },
            { id: "data-permissions", label: t("security.deepsec.tabs.dataPermissions"), icon: ShieldCheck },
          ]}
          idPrefix="security-deepsec"
          ariaLabel={t("security.deepsec.tabs.label")}
          onViewChange={(view) => {
            setActiveView(view);
            setActionError("");
            setResetError("");
          }}
        />

        {activeView === "data-user" ? (
          <ManagementPanelShell
            id="security-deepsec-panel-data-user"
            labelledBy="security-deepsec-tab-data-user"
            ariaLabel={t("security.deepsec.tabs.dataUser")}
          >
            <ManagementPanelHeader
              title={t("security.deepsec.config.title")}
              description={t("security.deepsec.tabs.dataUserHint")}
              icon={KeyRound}
            />
            <form
              className="grid gap-4"
              onSubmit={(event) => {
                event.preventDefault();
                void handleSaveConfig();
              }}
            >
              <div
                className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]"
                data-testid="security-deepsec-config-fields"
              >
                <dl className="rounded-md border border-border bg-background p-3 text-sm">
                  <dt className="text-muted">{t("security.deepsec.dataUser")}</dt>
                  <dd className="mt-1 break-all font-mono">
                    {status?.data_user ?? plan?.data_user ?? "DEEPSEC_DATA_USER"}
                  </dd>
                </dl>
                <div className="space-y-2">
                  <label htmlFor="deepsec-data-user-password" className="block text-sm font-medium">
                    {t("security.deepsec.config.password")}
                  </label>
                  <input
                    id="deepsec-data-user-password"
                    type="password"
                    autoComplete="new-password"
                    className={INPUT_CLASS}
                    value={dataUserPassword}
                    onChange={(event) => {
                      setDataUserPassword(event.target.value);
                      setConfigError("");
                    }}
                    placeholder={
                      hasSavedDataUserPassword
                        ? t("security.deepsec.config.passwordPlaceholderSaved")
                        : t("security.deepsec.config.passwordPlaceholderNew")
                    }
                    aria-describedby="deepsec-data-user-password-state"
                    aria-invalid={Boolean(configError)}
                  />
                  <p id="deepsec-data-user-password-state" className="text-xs text-muted">
                    {hasSavedDataUserPassword
                      ? t("security.deepsec.config.secretSaved")
                      : t("security.deepsec.config.secretMissing")}
                  </p>
                  {configError ? <FormStatus tone="danger" message={configError} /> : null}
                </div>
              </div>
              <div
                className="flex flex-wrap items-center gap-2 border-t border-border pt-4"
                data-testid="security-deepsec-config-actions"
              >
                <Button
                  type="submit"
                  size="lg"
                  loading={configSaving}
                  disabled={!dataUserPassword || configSaving || configSyncing}
                  className="h-[44px] w-full whitespace-nowrap sm:h-10 sm:w-auto"
                >
                  <Save size={15} aria-hidden />
                  {t("security.deepsec.config.save")}
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  size="lg"
                  loading={configSyncing}
                  disabled={passwordSyncDisabled}
                  className="h-[44px] w-full whitespace-nowrap sm:h-10 sm:w-auto"
                  onClick={() => void handleSyncConfig()}
                >
                  <RefreshCw size={15} aria-hidden />
                  {t("security.deepsec.config.sync")}
                </Button>
              </div>
            </form>
          </ManagementPanelShell>
        ) : null}

        {activeView === "foundation" ? (
          <ManagementPanelShell
            id="security-deepsec-panel-foundation"
            labelledBy="security-deepsec-tab-foundation"
            ariaLabel={t("security.deepsec.tabs.foundation")}
          >
            <ManagementPanelHeader
              title={t("security.deepsec.tabs.foundation")}
              description={t("security.deepsec.tabs.foundationHint")}
              icon={Settings2}
            />
            {actionError ? <FormStatus tone="danger" message={actionError} /> : null}
            {plan && !plan.has_data_user_password ? (
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0 flex-1">
                  <Banner severity="info">{t("security.deepsec.prerequisite.password")}</Banner>
                </div>
                <Button type="button" variant="secondary" size="sm" onClick={() => setActiveView("data-user")}>
                  {t("security.deepsec.actions.openDataUser")}
                </Button>
              </div>
            ) : null}
            <section className="grid gap-[16px]" aria-labelledby="security-deepsec-foundation-plan-title">
              <h3 id="security-deepsec-foundation-plan-title" className="text-base font-semibold">
                {t("security.deepsec.plan")}
              </h3>
              <DeepSecPlanSteps
                plan={plan}
                stepNumbers={[1, 2]}
                loading={planLoading}
                loadError={planLoadError}
                onRetry={() => void loadPlan()}
              />
              {foundationApplyVisible ? (
                <WorkSection
                  title={t("security.deepsec.applySectionTitle")}
                  description={t("security.deepsec.applySectionDescription")}
                  open={foundationApplyPanelOpen}
                  dataTestId="security-deepsec-foundation-apply-section"
                  onOpenChange={(open) => {
                    setFoundationApplyPanelOpen(open);
                    if (!open) {
                      setFoundationApplyConfirmation("");
                      setFoundationApplyError("");
                    }
                  }}
                >
                  <div className="grid gap-3">
                    {foundationApplyError ? <FormStatus tone="danger" message={foundationApplyError} /> : null}
                    <ExecutionConfirmationField
                      value={foundationApplyConfirmation}
                      onChange={(value) => {
                        setFoundationApplyConfirmation(value);
                        setFoundationApplyError("");
                      }}
                      confirmed={foundationApplyConfirmed}
                      placeholder={ADMIN_EXECUTE_CONFIRMATION}
                      expectedLabel={ADMIN_EXECUTE_CONFIRMATION}
                      helper={t("security.deepsec.applyFoundationHelper", {
                        phrase: ADMIN_EXECUTE_CONFIRMATION,
                      })}
                      tone="neutral"
                      disabled={foundationApplyBlocked || foundationApplying || resetting}
                      actions={
                        <>
                          <Button
                            type="button"
                            size="sm"
                            className="w-full sm:w-auto"
                            loading={foundationApplying}
                            disabled={
                              !foundationApplyConfirmed ||
                              foundationApplyBlocked ||
                              foundationApplying ||
                              resetting
                            }
                            onClick={() => void handleApplyFoundation()}
                          >
                            <Play size={15} aria-hidden />
                            {t("security.deepsec.applyFoundation")}
                          </Button>
                          <Button
                            type="button"
                            variant="secondary"
                            size="sm"
                            className="w-full sm:w-auto"
                            disabled={foundationApplying}
                            onClick={() => {
                              setFoundationApplyPanelOpen(false);
                              setFoundationApplyConfirmation("");
                              setFoundationApplyError("");
                            }}
                          >
                            {t("common.cancel")}
                          </Button>
                        </>
                      }
                    />
                  </div>
                </WorkSection>
              ) : null}
              {mayApply && resetAvailable ? (
                <WorkSection
                  title={t("security.deepsec.resetSectionTitle")}
                  description={t("security.deepsec.resetSectionDescription")}
                  tone="danger"
                  open={resetPanelOpen}
                  dataTestId="security-deepsec-reset-section"
                  onOpenChange={(open) => {
                    setResetPanelOpen(open);
                    if (open) setActionError("");
                    if (!open) {
                      setResetConfirmation("");
                      setResetError("");
                    }
                  }}
                >
                  <div className="grid gap-3">
                    <Banner severity="danger">{t("security.deepsec.resetWarning")}</Banner>
                    {resetError ? <FormStatus tone="danger" message={resetError} /> : null}
                    <ExecutionConfirmationField
                      value={resetConfirmation}
                      onChange={(value) => {
                        setResetConfirmation(value);
                        setResetError("");
                      }}
                      confirmed={resetConfirmed}
                      placeholder={ADMIN_RESET_CONFIRMATION}
                      expectedLabel={ADMIN_RESET_CONFIRMATION}
                      helper={t("security.deepsec.resetHelper", {
                        phrase: ADMIN_RESET_CONFIRMATION,
                      })}
                      tone="danger"
                      disabled={resetting}
                      actions={
                        <>
                          <Button
                            type="button"
                            variant="danger"
                            size="sm"
                            className="w-full sm:w-auto"
                            loading={resetting}
                            disabled={!resetConfirmed || resetting || foundationApplying}
                            onClick={() => void handleReset()}
                          >
                            <Trash2 size={15} aria-hidden />
                            {t("security.deepsec.reset")}
                          </Button>
                          <Button
                            type="button"
                            variant="secondary"
                            size="sm"
                            className="w-full sm:w-auto"
                            disabled={resetting}
                            onClick={() => {
                              setResetPanelOpen(false);
                              setResetConfirmation("");
                              setResetError("");
                            }}
                          >
                            {t("common.cancel")}
                          </Button>
                        </>
                      }
                    />
                  </div>
                </WorkSection>
              ) : null}
            </section>
          </ManagementPanelShell>
        ) : null}

        {activeView === "data-permissions" ? (
          <ManagementPanelShell
            id="security-deepsec-panel-data-permissions"
            labelledBy="security-deepsec-tab-data-permissions"
            ariaLabel={t("security.deepsec.tabs.dataPermissions")}
          >
            <ManagementPanelHeader
              title={t("security.deepsec.tabs.dataPermissions")}
              description={t("security.deepsec.tabs.dataPermissionsHint")}
              icon={ShieldCheck}
            />
            {actionError ? <FormStatus tone="danger" message={actionError} /> : null}
            {plan && !foundationReady ? (
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0 flex-1">
                  <Banner severity="info">{t("security.deepsec.prerequisite.foundation")}</Banner>
                </div>
                <Button type="button" variant="secondary" size="sm" onClick={() => setActiveView("foundation")}>
                  {t("security.deepsec.actions.openFoundation")}
                </Button>
              </div>
            ) : null}

            <Card>
              <CardHeader>
                <CardTitle className="flex min-w-0 items-center gap-2">
                  <Database size={18} aria-hidden />
                  <span className="min-w-0 break-words">{t("security.deepsec.entitlements.title")}</span>
                </CardTitle>
                <p className="text-sm leading-6 text-muted">{t("security.deepsec.entitlements.hint")}</p>
              </CardHeader>
              <CardContent className="space-y-4">
                {entitlementLoadError ? <Banner severity="danger">{entitlementLoadError}</Banner> : null}
                <div className="grid gap-4 xl:grid-cols-[minmax(15rem,22rem)_minmax(0,1fr)]">
                  <section className="grid min-w-0 content-start gap-3" aria-labelledby="deepsec-entitlement-role-list-title">
                    <h3 id="deepsec-entitlement-role-list-title" className="text-sm font-semibold">
                      {t("security.deepsec.entitlements.roles")}
                    </h3>
                    <SecuritySearchField
                      label={t("security.common.search")}
                      placeholder={t("security.deepsec.entitlements.searchPlaceholder")}
                      value={entitlementSearch}
                      testId="security-deepsec-entitlement-search"
                      onChange={setEntitlementSearch}
                    />
                    {entitlementLoading ? (
                      <ProcessingIndicator
                        active
                        label={t("security.deepsec.entitlements.loading")}
                        operationKey="security-deepsec-entitlements"
                        placement="panel"
                        testId="security-deepsec-entitlements-loading"
                        activityIcon="none"
                      />
                    ) : null}
                    <div
                      className="grid max-h-[17.5rem] gap-1.5 overflow-auto pr-1"
                      data-testid="security-deepsec-entitlement-roles"
                    >
                      {!entitlementLoading && filteredEntitlementRoles.length === 0 ? (
                        <p className="rounded-md border border-dashed border-border p-3 text-sm text-muted">
                          {entitlementSearch ? t("security.deepsec.entitlements.noResults") : t("security.common.empty")}
                        </p>
                      ) : null}
                      {filteredEntitlementRoles.map((role) => {
                        const selected = role.role_id === visibleSelectedEntitlementRoleId;
                        const statusBadge = entitlementRoleStatus(role);
                        return (
                          <button
                            key={role.role_id}
                            type="button"
                            className={cn(
                              "min-w-0 rounded-md border border-border bg-background p-2.5 text-left outline-none transition hover:border-primary/50 focus-visible:ring-2 focus-visible:ring-ring/40",
                              selected && "border-primary bg-primary/5"
                            )}
                            aria-pressed={selected}
                            data-testid={`security-deepsec-entitlement-role-${role.role_id}`}
                            onClick={() => {
                              setSelectedEntitlementRoleId(role.role_id);
                              setEntitlementFormError("");
                            }}
                          >
                            <span className="flex min-w-0 flex-wrap items-start justify-between gap-2">
                              <span className="min-w-0">
                                <span className="block break-words text-sm font-medium">{role.display_name}</span>
                                <span className="block break-all font-mono text-[11px] text-muted">{role.role_code}</span>
                              </span>
                              <StatusBadge variant={statusBadge.variant} label={statusBadge.label} />
                            </span>
                            <span className="mt-1.5 block text-xs text-muted">
                              {t("security.deepsec.entitlements.count", { count: role.data_entitlements.length })}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  </section>

                  <section className="min-w-0 rounded-md border border-border bg-background p-4" aria-labelledby="deepsec-entitlement-editor-title">
                    {!selectedEntitlementRole ? (
                      <div className="py-10 text-center">
                        <h3 id="deepsec-entitlement-editor-title" className="text-sm font-semibold">
                          {t("security.deepsec.entitlements.noSelectionTitle")}
                        </h3>
                        <p className="mt-1 text-sm text-muted">{t("security.deepsec.entitlements.noSelectionHint")}</p>
                      </div>
                    ) : (
                      <div
                        className="grid min-w-0 gap-3"
                        data-testid="security-deepsec-entitlement-form"
                        data-selected-entitlement-rule={selectedEntitlementDraft?.client_key}
                      >
                        <div className="grid min-w-0 gap-3 border-b border-border pb-3">
                          <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                            <div className="min-w-0">
                              <h3 id="deepsec-entitlement-editor-title" className="break-words text-base font-semibold">
                                {selectedEntitlementRole.display_name}
                              </h3>
                              <p className="mt-1 break-all font-mono text-xs text-muted">{selectedEntitlementRole.role_code}</p>
                            </div>
                            <div className="flex flex-wrap gap-2">
                              <StatusBadge
                                variant={entitlementRoleStatus(selectedEntitlementRole).variant}
                                label={entitlementRoleStatus(selectedEntitlementRole).label}
                              />
                              <StatusBadge
                                variant="info"
                                label={t("security.deepsec.entitlements.count", {
                                  count: entitlementDraftRows.length,
                                })}
                              />
                            </div>
                          </div>
                          {selectedEntitlementRole.is_built_in ? (
                            <Banner severity="info">{t("security.deepsec.entitlements.readOnlyBuiltIn")}</Banner>
                          ) : null}
                          {selectedEntitlementRole.archived ? (
                            <Banner severity="info">{t("security.deepsec.entitlements.readOnlyArchived")}</Banner>
                          ) : null}
                          {entitlementDraftChanged ? (
                            <Banner severity="info">{t("security.deepsec.entitlements.unsavedPreview")}</Banner>
                          ) : null}
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <h4 className="text-sm font-semibold">{t("security.deepsec.entitlements.rows")}</h4>
                            <div className="flex flex-wrap gap-2">
                              {selectedEntitlementDraft ? (
                                <Button
                                  type="button"
                                  size="sm"
                                  variant="ghost"
                                  aria-label={t("security.deepsec.entitlements.remove")}
                                  onClick={() => removeEntitlement(selectedEntitlementDraftIndex)}
                                  disabled={entitlementReadOnly || selectedEntitlementDraftIndex < 0}
                                >
                                  <Trash2 size={14} aria-hidden />
                                </Button>
                              ) : null}
                              <Button
                                type="button"
                                size="sm"
                                variant="secondary"
                                onClick={addEntitlement}
                                disabled={entitlementReadOnly}
                              >
                                <Plus size={14} aria-hidden />
                                {t("security.deepsec.entitlements.add")}
                              </Button>
                            </div>
                          </div>
                        </div>
                        <div
                          className="h-[36rem] min-h-0 md:h-[32rem]"
                          data-testid="security-deepsec-entitlement-workspace-frame"
                        >
                          {entitlementDraftRows.length === 0 ? (
                            <div
                              className="grid h-full content-center rounded-md border border-dashed border-border p-4 text-center"
                              data-testid="security-deepsec-entitlement-rules-empty"
                            >
                              <h4 className="text-sm font-semibold">
                                {t("security.deepsec.entitlements.emptyRulesTitle")}
                              </h4>
                              <p className="mt-1 text-sm text-muted">
                                {t("security.deepsec.entitlements.emptyRulesHint")}
                              </p>
                            </div>
                          ) : (
                            <div
                              className="grid h-full min-h-0 grid-rows-[auto_minmax(0,1fr)] gap-3 overflow-hidden 2xl:grid-cols-[minmax(12rem,18rem)_minmax(0,1fr)] 2xl:grid-rows-1"
                              data-testid="security-deepsec-entitlement-workspace"
                            >
                            <div
                              className="grid max-h-36 min-h-0 content-start gap-2 overflow-y-auto pr-1 2xl:max-h-none"
                              aria-label={t("security.deepsec.entitlements.ruleList")}
                              data-testid="security-deepsec-entitlement-rules-list"
                            >
                              {entitlementDraftRows.map((entitlement, index) => {
                                const targetKey = entitlementTargetKey(entitlement);
                                const detail = targetDetails[targetKey];
                                const statusBadge = entitlementApplyStatus(entitlement);
                                const ruleTitle =
                                  targetKey || t("security.deepsec.entitlements.ruleTitle");
                                const selected = entitlement.client_key === visibleSelectedEntitlementDraftKey;
                                return (
                                  <button
                                    key={entitlement.client_key}
                                    type="button"
                                    className={cn(
                                      "min-w-0 rounded-md border border-border bg-card/30 p-3 text-left outline-none transition hover:border-primary/50 focus-visible:ring-2 focus-visible:ring-ring/40",
                                      selected && "border-primary bg-primary/5"
                                    )}
                                    aria-pressed={selected}
                                    data-testid={`security-deepsec-entitlement-rule-tab-${index}`}
                                    onClick={() => {
                                      setSelectedEntitlementDraftKey(entitlement.client_key);
                                      setEntitlementFormError("");
                                    }}
                                  >
                                    <span className="flex min-w-0 flex-wrap items-start justify-between gap-2">
                                      <span className="min-w-0">
                                        <span className="block break-all text-sm font-semibold">{ruleTitle}</span>
                                        <span className="mt-1 block break-all font-mono text-[11px] text-muted">
                                          {entitlement.data_grant_name ||
                                            t("security.deepsec.entitlements.notGenerated")}
                                        </span>
                                      </span>
                                      <StatusBadge variant={statusBadge.variant} label={statusBadge.label} />
                                    </span>
                                    <span className="mt-2 flex min-w-0 flex-wrap gap-2 text-[11px] text-muted">
                                      <span>{entitlementColumnsSummary(entitlement, detail)}</span>
                                      <span aria-hidden="true">/</span>
                                      <span>{entitlementScopeSummary(entitlement)}</span>
                                    </span>
                                  </button>
                                );
                              })}
                            </div>
                            <div
                              className="min-h-0 overflow-y-auto pr-1"
                              ref={entitlementEditorScrollRef}
                              data-testid={
                                selectedEntitlementDraft
                                  ? `security-deepsec-entitlement-rule-${selectedEntitlementDraftIndex}`
                                  : "security-deepsec-entitlement-editor-empty"
                              }
                            >
                              {selectedEntitlementDraft ? (
                                (() => {
                                  const entitlement = selectedEntitlementDraft;
                                  const index = selectedEntitlementDraftIndex;
                                  const targetKey = entitlementTargetKey(entitlement);
                                  const detail = targetDetails[targetKey];
                                  const loadingDetail = Boolean(targetDetailLoading[targetKey]);
                                  const detailError = targetDetailErrors[targetKey] ?? "";
                                  const selectedColumns = new Set(
                                    (entitlement.column_names ?? []).map((column) => column.toUpperCase())
                                  );
                                  const availableColumnNames =
                                    detail?.columns.map((column) => column.column_name.toUpperCase()) ?? [];
                                  const selectedAvailableColumnCount = availableColumnNames.filter(
                                    (columnName) => selectedColumns.has(columnName)
                                  ).length;
                                  const statusBadge = entitlementApplyStatus(entitlement);
                                  const supportedScopeColumns =
                                    detail?.columns.filter(isSupportedScopeColumn) ?? [];
                                  const scopeFilters = entitlement.scope_filters ?? [];
                                  const ruleTitle =
                                    targetKey || t("security.deepsec.entitlements.ruleTitle");
                                return (
                                  <section
                                    className="min-w-0 rounded-md border border-border bg-background"
                                  >
                                    <div className="flex min-h-12 items-start justify-between gap-3 border-b border-border px-3 py-3">
                                      <div className="min-w-0">
                                        <p
                                          className="break-all text-sm font-semibold"
                                          data-testid={`security-deepsec-entitlement-editor-title-${index}`}
                                        >
                                          {ruleTitle}
                                        </p>
                                        <p className="mt-1 break-all font-mono text-[11px] text-muted">
                                          {entitlement.data_grant_name ||
                                            t("security.deepsec.entitlements.notGenerated")}
                                        </p>
                                        <p className="mt-1 text-[11px] text-muted">
                                          {entitlementColumnsSummary(entitlement, detail)}
                                          {" / "}
                                          {entitlementScopeSummary(entitlement)}
                                        </p>
                                      </div>
                                      <div className="flex shrink-0 items-center gap-2">
                                        <StatusBadge variant={statusBadge.variant} label={statusBadge.label} />
                                      </div>
                                    </div>
                                    <div className="grid gap-3 p-3">
                                      {entitlement.apply_error_message ? (
                                        <FormStatus tone="danger" message={entitlement.apply_error_message} />
                                      ) : null}
                                      <DeepSecTargetObjectPicker
                                        index={index}
                                        value={targetKey}
                                        selectedObject={targetObjectMap.get(targetKey) ?? null}
                                        objects={visibleTargetObjects}
                                        total={targetObjectTotal}
                                        nextCursor={targetObjectNextCursor}
                                        search={targetObjectSearch}
                                        ownerPrefix={targetObjectOwnerPrefix}
                                        loading={targetObjectsLoading}
                                        loadingMore={targetObjectsLoadingMore}
                                        error={targetObjectLoadError}
                                        disabled={entitlementReadOnly}
                                        onSearchChange={setTargetObjectSearch}
                                        onOwnerPrefixChange={setTargetObjectOwnerPrefix}
                                        onSelect={(value) => updateEntitlementTarget(index, value)}
                                        onLoadMore={() =>
                                          void loadTargetObjects({
                                            cursor: targetObjectNextCursor,
                                            append: true,
                                          })
                                        }
                                      />
                                      <fieldset className="grid gap-2">
                                        <FieldLegend className="text-xs font-medium" required>
                                          {t("security.deepsec.entitlements.columns")}
                                        </FieldLegend>
                                        <div className="mt-1 flex min-w-0 justify-start">
                                          <BulkSelectionActions
                                            selectLabel={t("common.selection.selectAll")}
                                            clearLabel={t("common.selection.clearAll")}
                                            selectDisabled={
                                              entitlementReadOnly ||
                                              loadingDetail ||
                                              Boolean(detailError) ||
                                              availableColumnNames.length === 0 ||
                                              selectedAvailableColumnCount === availableColumnNames.length
                                            }
                                            clearDisabled={
                                              entitlementReadOnly ||
                                              loadingDetail ||
                                              Boolean(detailError) ||
                                              availableColumnNames.length === 0 ||
                                              selectedAvailableColumnCount === 0
                                            }
                                            dataTestId={`security-deepsec-entitlement-column-selection-actions-${index}`}
                                            onSelectAll={() =>
                                              setEntitlementColumnsPreservingScroll(
                                                index,
                                                availableColumnNames
                                              )
                                            }
                                            onClearAll={() =>
                                              setEntitlementColumnsPreservingScroll(index, [])
                                            }
                                          />
                                        </div>
                                        {loadingDetail ? (
                                          <p className="rounded-md border border-dashed border-border p-3 text-sm text-muted">
                                            {t("security.deepsec.entitlements.columnsLoading")}
                                          </p>
                                        ) : detailError ? (
                                          <p className="rounded-md border border-danger/30 bg-danger-bg p-3 text-sm text-danger">
                                            {detailError}
                                          </p>
                                        ) : detail?.columns.length ? (
                                          <div
                                            className="mt-1 grid max-h-48 gap-2 overflow-auto rounded-md border border-border bg-background p-2 sm:grid-cols-2 xl:grid-cols-3"
                                            data-entitlement-scroll-container
                                            data-testid={`security-deepsec-entitlement-columns-grid-${index}`}
                                          >
                                            {detail.columns.map((column) => (
                                              <label
                                                key={column.column_name}
                                                className="flex min-h-11 min-w-0 items-start gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-muted/20"
                                              >
                                                <input
                                                  type="checkbox"
                                                  className="mt-1 h-4 w-4 shrink-0 accent-primary"
                                                  disabled={entitlementReadOnly}
                                                  checked={selectedColumns.has(
                                                    column.column_name.toUpperCase()
                                                  )}
                                                  onChange={(event) =>
                                                    toggleEntitlementColumn(
                                                      index,
                                                      column.column_name,
                                                      event.target.checked
                                                    )
                                                  }
                                                />
                                                <span className="min-w-0">
                                                  <span className="block break-all font-mono text-xs">
                                                    {column.column_name}
                                                  </span>
                                                  <span className="block text-[11px] text-muted">
                                                    {column.data_type}
                                                  </span>
                                                </span>
                                              </label>
                                            ))}
                                          </div>
                                        ) : (
                                          <p className="rounded-md border border-dashed border-border p-3 text-sm text-muted">
                                            {t("security.deepsec.entitlements.selectObjectForColumns")}
                                          </p>
                                        )}
                                      </fieldset>
                                    <FieldLabel
                                      className="block text-xs font-medium"
                                      htmlFor={`deepsec-entitlement-scope-mode-${index}`}
                                      label={
                                        <span data-testid={`security-deepsec-scope-mode-label-text-${index}`}>
                                          {t("security.deepsec.entitlements.scopeMode")}
                                        </span>
                                      }
                                      required
                                    >
                                      <select
                                        id={`deepsec-entitlement-scope-mode-${index}`}
                                        className={cn(INPUT_CLASS, "mt-1 block")}
                                        disabled={entitlementReadOnly}
                                        value={entitlement.scope_mode ?? "ALL"}
                                        onChange={(event) => {
                                          const nextMode = event.target.value;
                                          patchEntitlement(index, {
                                            scope_mode: nextMode,
                                            scope_code: nextMode === "ALL" ? "*" : nextMode === "FILTERS" ? "FILTERS" : "",
                                            scope_column: "",
                                            scope_filters:
                                              nextMode === "FILTERS"
                                                ? entitlement.scope_filters?.length
                                                  ? entitlement.scope_filters
                                                  : [blankScopeFilter(supportedScopeColumns)]
                                                : [],
                                          });
                                        }}
                                      >
                                        <option value="ALL">
                                          {t("security.deepsec.entitlements.scopeAll")}
                                        </option>
                                        <option value="FILTERS">
                                          {t("security.deepsec.entitlements.scopeFilters")}
                                        </option>
                                      </select>
                                    </FieldLabel>
                                    {entitlement.scope_mode === "FILTERS" ? (
                                      <div
                                        className="grid gap-2 rounded-md border border-border bg-background p-3"
                                        data-testid={`security-deepsec-scope-filters-${index}`}
                                      >
                                        <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                                          <p className="text-xs font-medium">
                                            {t("security.deepsec.entitlements.scopeFilters")}
                                          </p>
                                          <Button
                                            type="button"
                                            size="sm"
                                            variant="secondary"
                                            className="w-full sm:w-auto"
                                            disabled={
                                              entitlementReadOnly ||
                                              loadingDetail ||
                                              supportedScopeColumns.length === 0
                                            }
                                            onClick={() => addScopeFilter(index, supportedScopeColumns)}
                                          >
                                            <Plus size={14} aria-hidden />
                                            {t("security.deepsec.entitlements.scopeFilterAdd")}
                                          </Button>
                                        </div>
                                        {loadingDetail ? (
                                          <p className="rounded-md border border-dashed border-border p-3 text-sm text-muted">
                                            {t("security.deepsec.entitlements.columnsLoading")}
                                          </p>
                                        ) : detailError ? (
                                          <p className="rounded-md border border-danger/30 bg-danger-bg p-3 text-sm text-danger">
                                            {detailError}
                                          </p>
                                        ) : !detail ? (
                                          <p className="rounded-md border border-dashed border-border p-3 text-sm text-muted">
                                            {t("security.deepsec.entitlements.scopeFilterSelectObject")}
                                          </p>
                                        ) : supportedScopeColumns.length === 0 ? (
                                          <p className="rounded-md border border-dashed border-border p-3 text-sm text-muted">
                                            {t("security.deepsec.entitlements.scopeFilterUnsupported")}
                                          </p>
                                        ) : scopeFilters.length === 0 ? (
                                          <p className="rounded-md border border-dashed border-border p-3 text-sm text-muted">
                                            {t("security.deepsec.entitlements.scopeFilterEmpty")}
                                          </p>
                                        ) : (
                                          <div className="grid gap-2">
                                            {scopeFilters.map((filter, filterIndex) => {
                                              const selectedColumn = supportedScopeColumns.find(
                                                (column) =>
                                                  column.column_name.toUpperCase() ===
                                                  filter.column_name.toUpperCase()
                                              );
                                              const valueType =
                                                selectedColumn
                                                  ? scopeValueType(selectedColumn.data_type) ?? "TEXT"
                                                  : (filter.value_type as DataEntitlementScopeValueType);
                                              const operators =
                                                SCOPE_OPERATORS_BY_VALUE_TYPE[valueType] ??
                                                SCOPE_OPERATORS_BY_VALUE_TYPE.TEXT;
                                              const selectedOperator = operators.includes(
                                                filter.operator as DataEntitlementScopeOperator
                                              )
                                                ? (filter.operator as DataEntitlementScopeOperator)
                                                : operators[0];
                                              const inputMode =
                                                valueType === "NUMBER" && selectedOperator === "EQ"
                                                  ? "numeric"
                                                  : valueType === "NUMBER"
                                                    ? "decimal"
                                                    : "text";
                                              const selectedValueSource = scopeFilterSupportsValueSource(
                                                selectedOperator,
                                                valueType
                                              )
                                                ? normalizeScopeFilterValueSource(filter.value_source)
                                                : LITERAL_SCOPE_VALUE_SOURCE;
                                              return (
                                                <div
                                                  key={`${index}-${filterIndex}-${filter.column_name}`}
                                                  className="grid gap-2 rounded-md border border-border/80 bg-card/20 p-2"
                                                  data-testid={`security-deepsec-scope-filter-${index}-${filterIndex}`}
                                                >
                                                  <div className="grid min-w-0 gap-2 md:grid-cols-2 2xl:grid-cols-[minmax(15rem,1.25fr)_minmax(9rem,0.75fr)_minmax(10rem,0.8fr)_minmax(12rem,1fr)_auto]">
                                                    <label
                                                      className="grid min-w-0 gap-1 text-xs font-medium"
                                                      htmlFor={`deepsec-scope-filter-column-${index}-${filterIndex}`}
                                                    >
                                                      <span>
                                                        {t("security.deepsec.entitlements.scopeFilterColumn")}
                                                      </span>
                                                      <select
                                                        id={`deepsec-scope-filter-column-${index}-${filterIndex}`}
                                                        className={cn(COMPACT_INPUT_CLASS, "min-w-0")}
                                                        disabled={entitlementReadOnly}
                                                        value={filter.column_name}
                                                        onChange={(event) =>
                                                          updateScopeFilterColumn(
                                                            index,
                                                            filterIndex,
                                                            event.target.value,
                                                            supportedScopeColumns
                                                          )
                                                        }
                                                      >
                                                        <option value="">
                                                          {t(
                                                            "security.deepsec.entitlements.scopeFilterColumnPlaceholder"
                                                          )}
                                                        </option>
                                                        {supportedScopeColumns.map((column) => (
                                                          <option
                                                            key={column.column_name}
                                                            value={column.column_name}
                                                          >
                                                            {column.column_name} · {column.data_type}
                                                          </option>
                                                        ))}
                                                      </select>
                                                    </label>
                                                    <label
                                                      className="grid min-w-0 gap-1 text-xs font-medium"
                                                      htmlFor={`deepsec-scope-filter-operator-${index}-${filterIndex}`}
                                                    >
                                                      <span>
                                                        {t("security.deepsec.entitlements.scopeFilterOperator")}
                                                      </span>
                                                      <select
                                                        id={`deepsec-scope-filter-operator-${index}-${filterIndex}`}
                                                        className={COMPACT_INPUT_CLASS}
                                                        disabled={entitlementReadOnly}
                                                        value={selectedOperator}
                                                        onChange={(event) =>
                                                          updateScopeFilterOperator(
                                                            index,
                                                            filterIndex,
                                                            event.target.value,
                                                            valueType
                                                          )
                                                        }
                                                      >
                                                        {operators.map((operator) => (
                                                          <option key={operator} value={operator}>
                                                            {t(
                                                              `security.deepsec.entitlements.operator.${operator}`
                                                            )}
                                                          </option>
                                                        ))}
                                                      </select>
                                                    </label>
                                                    {scopeFilterSupportsValueSource(
                                                      selectedOperator,
                                                      valueType
                                                    ) ? (
                                                      <label
                                                        className="grid min-w-0 gap-1 text-xs font-medium"
                                                        htmlFor={`deepsec-scope-filter-value-source-${index}-${filterIndex}`}
                                                      >
                                                        <span>
                                                          {t("security.deepsec.entitlements.scopeFilterValueSource")}
                                                        </span>
                                                        <select
                                                          id={`deepsec-scope-filter-value-source-${index}-${filterIndex}`}
                                                          className={COMPACT_INPUT_CLASS}
                                                          disabled={entitlementReadOnly}
                                                          value={selectedValueSource}
                                                          onChange={(event) =>
                                                            patchScopeFilter(index, filterIndex, {
                                                              value_source: event.target.value,
                                                              value: "",
                                                              value_to: "",
                                                              values: [],
                                                              value_type: valueType,
                                                            })
                                                          }
                                                        >
                                                          <option value={LITERAL_SCOPE_VALUE_SOURCE}>
                                                            {t(
                                                              "security.deepsec.entitlements.scopeFilterValueLiteral"
                                                            )}
                                                          </option>
                                                          <option value={LOGIN_USER_ID_SCOPE_VALUE_SOURCE}>
                                                            {t(
                                                              "security.deepsec.entitlements.scopeFilterValueLoginUserId"
                                                            )}
                                                          </option>
                                                        </select>
                                                      </label>
                                                    ) : null}
                                                    {scopeFilterNeedsValues(selectedOperator) ? (
                                                      <label
                                                        className="grid min-w-0 gap-1 text-xs font-medium"
                                                        htmlFor={`deepsec-scope-filter-values-${index}-${filterIndex}`}
                                                      >
                                                        <span>
                                                          {t("security.deepsec.entitlements.scopeFilterValues")}
                                                        </span>
                                                        <input
                                                          id={`deepsec-scope-filter-values-${index}-${filterIndex}`}
                                                          className={COMPACT_INPUT_CLASS}
                                                          disabled={entitlementReadOnly}
                                                          inputMode={inputMode}
                                                          value={(filter.values ?? []).join(", ")}
                                                          onChange={(event) =>
                                                            patchScopeFilter(index, filterIndex, {
                                                              value: "",
                                                              value_to: "",
                                                              values: event.target.value
                                                                .split(",")
                                                                .map((value) => value.trim())
                                                                .filter(Boolean),
                                                              value_type: valueType,
                                                            })
                                                          }
                                                        />
                                                      </label>
                                                    ) : scopeFilterNeedsValueTo(selectedOperator) ? (
                                                      <div className="grid min-w-0 gap-2 sm:grid-cols-2">
                                                        <label
                                                          className="grid min-w-0 gap-1 text-xs font-medium"
                                                          htmlFor={`deepsec-scope-filter-value-${index}-${filterIndex}`}
                                                        >
                                                          <span>
                                                            {t("security.deepsec.entitlements.scopeFilterValue")}
                                                          </span>
                                                          <input
                                                            id={`deepsec-scope-filter-value-${index}-${filterIndex}`}
                                                            className={COMPACT_INPUT_CLASS}
                                                            disabled={entitlementReadOnly}
                                                            inputMode={inputMode}
                                                            value={filter.value ?? ""}
                                                            onChange={(event) =>
                                                              patchScopeFilter(index, filterIndex, {
                                                                value: event.target.value,
                                                                values: [],
                                                                value_type: valueType,
                                                              })
                                                            }
                                                          />
                                                        </label>
                                                        <label
                                                          className="grid min-w-0 gap-1 text-xs font-medium"
                                                          htmlFor={`deepsec-scope-filter-value-to-${index}-${filterIndex}`}
                                                        >
                                                          <span>
                                                            {t("security.deepsec.entitlements.scopeFilterValueTo")}
                                                          </span>
                                                          <input
                                                            id={`deepsec-scope-filter-value-to-${index}-${filterIndex}`}
                                                            className={COMPACT_INPUT_CLASS}
                                                            disabled={entitlementReadOnly}
                                                            inputMode={inputMode}
                                                            value={filter.value_to ?? ""}
                                                            onChange={(event) =>
                                                              patchScopeFilter(index, filterIndex, {
                                                                value_to: event.target.value,
                                                                values: [],
                                                                value_type: valueType,
                                                              })
                                                            }
                                                          />
                                                        </label>
                                                      </div>
                                                    ) : scopeFilterNeedsValue(selectedOperator) &&
                                                      selectedValueSource === LOGIN_USER_ID_SCOPE_VALUE_SOURCE ? (
                                                      <div className="grid min-w-0 gap-1 text-xs font-medium">
                                                        <span>
                                                          {t("security.deepsec.entitlements.scopeFilterValue")}
                                                        </span>
                                                        <p
                                                          className="min-h-9 rounded-md border border-border bg-muted/20 px-3 py-2 text-xs font-normal text-muted"
                                                          data-testid={`security-deepsec-scope-filter-login-user-id-${index}-${filterIndex}`}
                                                        >
                                                          {t(
                                                            "security.deepsec.entitlements.scopeFilterValueLoginUserIdHelper"
                                                          )}
                                                        </p>
                                                      </div>
                                                    ) : scopeFilterNeedsValue(selectedOperator) ? (
                                                      <label
                                                        className="grid min-w-0 gap-1 text-xs font-medium"
                                                        htmlFor={`deepsec-scope-filter-value-${index}-${filterIndex}`}
                                                      >
                                                        <span>
                                                          {t("security.deepsec.entitlements.scopeFilterValue")}
                                                        </span>
                                                        <input
                                                          id={`deepsec-scope-filter-value-${index}-${filterIndex}`}
                                                          className={COMPACT_INPUT_CLASS}
                                                          disabled={entitlementReadOnly}
                                                          inputMode={inputMode}
                                                          pattern={
                                                            valueType === "NUMBER" &&
                                                            selectedOperator === "EQ"
                                                              ? "[0-9]*"
                                                              : undefined
                                                          }
                                                          value={filter.value ?? ""}
                                                          onChange={(event) =>
                                                            patchScopeFilter(index, filterIndex, {
                                                              value: event.target.value,
                                                              value_to: "",
                                                              values: [],
                                                              value_type: valueType,
                                                            })
                                                          }
                                                        />
                                                      </label>
                                                    ) : (
                                                      <p className="self-end rounded-md border border-dashed border-border px-3 py-2 text-xs text-muted md:col-span-2 2xl:col-span-1">
                                                        {t("security.deepsec.entitlements.scopeFilterNoValue")}
                                                      </p>
                                                    )}
                                                    <Button
                                                      type="button"
                                                      size="sm"
                                                      variant="ghost"
                                                      className="justify-self-end self-end md:col-span-2 2xl:col-span-1"
                                                      aria-label={t(
                                                        "security.deepsec.entitlements.scopeFilterRemove"
                                                      )}
                                                      disabled={entitlementReadOnly}
                                                      onClick={() => removeScopeFilter(index, filterIndex)}
                                                    >
                                                      <Trash2 size={14} aria-hidden />
                                                    </Button>
                                                  </div>
                                                </div>
                                              );
                                            })}
                                          </div>
                                        )}
                                      </div>
                                    ) : null}
                                  </div>
                                  </section>
                                );
                                })()
                              ) : (
                                <div className="grid content-center rounded-md border border-dashed border-border p-4 text-center">
                                  <p className="text-sm text-muted">
                                    {t("security.deepsec.entitlements.noSelectionHint")}
                                  </p>
                                </div>
                              )}
                            </div>
                            </div>
                          )}
                        </div>
                        <div
                          className="grid min-h-0 gap-3 border-t border-border bg-card/60 pt-3"
                          data-testid="security-deepsec-entitlement-action-region"
                        >
                          {entitlementFormError ? <FormStatus tone="danger" message={entitlementFormError} /> : null}
                          <details
                            open={entitlementSqlPreviewOpen}
                            className="group/disclosure max-h-[min(28rem,45dvh)] min-w-0 overflow-y-auto overscroll-contain rounded-md border border-border bg-background"
                            data-testid="security-deepsec-sql-preview"
                          >
                            <summary
                              className="sticky top-0 z-10 flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 bg-background px-3 py-2 text-sm font-semibold outline-none focus-visible:ring-2 focus-visible:ring-ring/40 [&::-webkit-details-marker]:hidden"
                              onClick={(event) => {
                                event.preventDefault();
                                setEntitlementSqlPreviewOpen((current) => !current);
                              }}
                            >
                              <span>{t("security.deepsec.entitlements.sqlPreview")}</span>
                              <DisclosureChevron
                                expanded="group"
                                size={16}
                                className="text-muted"
                              />
                            </summary>
                            <div className="space-y-3 border-t border-border p-3">
                              <div
                                className="grid min-w-0 gap-2"
                                data-testid="security-deepsec-sql-preview-toolbar"
                              >
                                <p className="min-w-0 text-sm leading-6 text-muted">
                                  {t("security.deepsec.entitlements.sqlReadonly")}
                                </p>
                                <div className="flex min-w-0 justify-end">
                                  <Button
                                    type="button"
                                    variant="secondary"
                                    size="sm"
                                    className="w-full min-w-0 justify-center lg:w-auto"
                                    loading={entitlementPreviewing}
                                    disabled={
                                      entitlementReadOnly ||
                                      entitlementPreviewing ||
                                      entitlementApplying ||
                                      (normalizedEntitlementDraftRows.length === 0 &&
                                        savedEntitlementRows.length === 0)
                                    }
                                    data-testid="security-deepsec-sql-preview-generate"
                                    onClick={() => void handlePreviewEntitlements()}
                                  >
                                    <RefreshCw size={14} aria-hidden />
                                    {t("security.deepsec.entitlements.generatePreview")}
                                  </Button>
                                </div>
                              </div>
                              {entitlementPreview ? (
                                <div className="grid min-w-0 gap-4">
                                  <p className="text-sm leading-6 text-muted" role="status">
                                    {t("security.deepsec.entitlements.sqlScopeSummary", {
                                      role: selectedEntitlementRole.display_name,
                                      grantCount: selectedRolePreviewRows.length,
                                      cleanupCount: selectedRoleCleanupSql.length,
                                      sqlCount: selectedRolePreviewSqlCount,
                                    })}
                                  </p>
                                  <div className="space-y-1">
                                    <p className="text-xs font-medium text-muted">
                                      {t("security.deepsec.checksum")}
                                    </p>
                                    <code className="block break-all rounded-md bg-card p-2 text-[11px]">
                                      {entitlementPreview.checksum}
                                    </code>
                                  </div>
                                  {selectedRoleCleanupSql.length ? (
                                    <section className="grid min-w-0 gap-2" aria-labelledby="deepsec-cleanup-sql-title">
                                      <h5 id="deepsec-cleanup-sql-title" className="text-sm font-semibold text-danger">
                                        {t("security.deepsec.entitlements.sqlCleanupTitle")}
                                      </h5>
                                      {selectedRoleCleanupSql.map((sql, index) => (
                                        <pre
                                          key={`${selectedEntitlementRole.role_id}-cleanup-${index}`}
                                          className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-md border border-danger/30 bg-code p-3 text-code-fg text-xs leading-5"
                                          tabIndex={0}
                                          aria-label={t("security.deepsec.entitlements.sqlCleanupAria", {
                                            index: index + 1,
                                          })}
                                        >
                                          {sql}
                                        </pre>
                                      ))}
                                    </section>
                                  ) : null}
                                  {selectedRolePreviewRows.length ? (
                                    <section className="grid min-w-0 gap-3" aria-labelledby="deepsec-apply-sql-title">
                                      <h5 id="deepsec-apply-sql-title" className="text-sm font-semibold">
                                        {t("security.deepsec.entitlements.sqlApplyTitle")}
                                      </h5>
                                      {selectedRolePreviewRows.map((item) => {
                                        const target = entitlementTargetKey(item);
                                        const dataGrant =
                                          item.data_grant_name ||
                                          t("security.deepsec.entitlements.notGenerated");
                                        return (
                                          <article
                                            key={item.entitlement_id ?? `${target}-${dataGrant}`}
                                            className="grid min-w-0 gap-2 rounded-md border border-border p-3"
                                          >
                                            <h6 className="min-w-0 break-words text-xs font-semibold">
                                              {t("security.deepsec.entitlements.sqlGrantHeading", {
                                                target,
                                                dataGrant,
                                              })}
                                            </h6>
                                            {(item.sql ?? []).map((sql, index) => (
                                              <pre
                                                key={`${item.entitlement_id ?? dataGrant}-sql-${index}`}
                                                className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border bg-code p-3 text-code-fg text-xs leading-5"
                                                tabIndex={0}
                                                aria-label={t("security.deepsec.entitlements.sqlGrantAria", {
                                                  target,
                                                  index: index + 1,
                                                })}
                                              >
                                                {sql}
                                              </pre>
                                            ))}
                                          </article>
                                        );
                                      })}
                                    </section>
                                  ) : null}
                                  {selectedRolePreviewSqlCount === 0 ? (
                                    <p className="rounded-md border border-dashed border-border p-3 text-sm text-muted">
                                      {t("security.deepsec.entitlements.sqlNoChanges")}
                                    </p>
                                  ) : null}
                                </div>
                              ) : (
                                <p className="rounded-md border border-dashed border-border p-3 text-sm text-muted">
                                  {t("security.deepsec.entitlements.sqlEmpty")}
                                </p>
                              )}
                            </div>
                          </details>
                          <ExecutionConfirmationField
                            value={entitlementApplyConfirmation}
                            onChange={(value) => {
                              setEntitlementApplyConfirmation(value);
                              setEntitlementFormError("");
                            }}
                            confirmed={entitlementApplyConfirmed}
                            placeholder={ADMIN_EXECUTE_CONFIRMATION}
                            expectedLabel={ADMIN_EXECUTE_CONFIRMATION}
                            helper={t("security.deepsec.entitlements.applyHelper", {
                              phrase: ADMIN_EXECUTE_CONFIRMATION,
                            })}
                            tone="danger"
                            disabled={
                              entitlementReadOnly ||
                              entitlementPreviewing ||
                              entitlementApplying ||
                              !status?.configured
                            }
                            actions={
                              <Button
                                type="button"
                                variant="danger"
                                size="sm"
                                className="w-full sm:w-auto"
                                loading={entitlementApplying}
                                disabled={
                                  entitlementReadOnly ||
                                  entitlementPreviewing ||
                                  entitlementApplying ||
                                  !status?.configured ||
                                  !entitlementApplyConfirmed
                                }
                                onClick={() => void handleApplyEntitlements()}
                              >
                                <ShieldCheck size={15} aria-hidden />
                                {t("security.deepsec.entitlements.apply")}
                              </Button>
                            }
                          />
                        </div>
                      </div>
                    )}
                  </section>
                </div>
              </CardContent>
            </Card>

            {mayVerify ? (
              <Card data-testid="security-deepsec-verification-card">
                <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div className="min-w-0">
                    <CardTitle>{t("security.deepsec.result")}</CardTitle>
                    <p className="mt-1 text-sm leading-6 text-muted">
                      {t("security.deepsec.resultEmpty")}
                    </p>
                  </div>
                  <div className="flex shrink-0 flex-col gap-2 sm:items-end">
                    {verification ? (
                      <StatusBadge
                        variant={verification.passed ? "success" : "warning"}
                        label={verification.passed ? t("security.deepsec.complete") : t("security.deepsec.failed")}
                      />
                    ) : null}
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      className="w-full sm:w-auto"
                      loading={verifying}
                      disabled={!status?.configured || verifying}
                      data-testid="security-deepsec-verify-action"
                      onClick={() => void handleVerify()}
                    >
                      <ShieldCheck size={14} aria-hidden />
                      {t("security.deepsec.verify")}
                    </Button>
                  </div>
                </CardHeader>
                <CardContent className="space-y-2">
                  {verification ? (
                    <div
                      role="region"
                      aria-label={t("security.deepsec.resultListAriaLabel")}
                      tabIndex={0}
                      className="grid max-h-[23.25rem] gap-2 overflow-y-auto overscroll-contain pr-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
                      data-testid="security-deepsec-verification-results"
                    >
                      {verification.checks.map((check) => (
                        <div
                          key={check.key}
                          className="flex min-h-[4.25rem] items-start gap-2 rounded-md border border-border p-3 text-sm"
                        >
                          <CheckCircle2 size={16} className={check.passed ? "text-success" : "text-warning"} aria-hidden />
                          <div className="min-w-0">
                            <p className="font-mono text-xs font-medium">{check.key}</p>
                            <p className="mt-1 break-words text-muted">{check.detail}</p>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="rounded-md border border-dashed border-border p-3 text-sm leading-6 text-muted">
                      {t("security.deepsec.resultPending")}
                    </p>
                  )}
                </CardContent>
              </Card>
            ) : null}
          </ManagementPanelShell>
        ) : null}
      </main>
    </>
  );
}
