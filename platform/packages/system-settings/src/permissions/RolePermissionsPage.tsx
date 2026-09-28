import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { useSearchParams } from "react-router-dom";
import { ArrowLeft, LockKeyhole, Pencil, Plus, RefreshCw, ShieldCheck } from "lucide-react";
import {
  Banner,
  BulkSelectionActions,
  Button,
  DataTable,
  EmptyState,
  FieldError,
  FormStatus,
  ObjectActionBar,
  PageBody,
  PageHeader,
  ProcessingIndicator,
  StatusBadge,
  TextField,
  toast,
  useConfirm,
  type DataTableColumn,
  type DataTableSort,
  type EntityAction,
  FormActionBar,
  INFORMATION_LIST_SCROLL_CLASS,
  INFORMATION_TABLE_FOCUS_CLASS,
  INFORMATION_TABLE_ROW_CLASS,
  INFORMATION_TABLE_VISIBLE_ROWS,
} from "@engchina/production-ready-ui";

import { formatMessage } from "../auth/messages";
import { useUnsavedChangesGuard } from "../guards/useUnsavedChangesGuard";
import { useRequestScope } from "../oci/useRequestScope";
import { RoleStatusBadges } from "../users-roles/RoleManagementPage";
import {
  SecurityDetailField,
  SecurityEmptySelection,
  SecurityIdentityLines,
  SecurityManagementPanelShell,
  SecurityPanelHeader,
  SecuritySearchField,
  identitySecondaryName,
  isAbortError,
  securityFilteredCount,
  selectedVisibleKey,
  useValuesChanged,
} from "../users-roles/shared";
import { SYSTEM_ADMIN_ROLE_CODE } from "../users-roles/types";
import { ROLE_PERMISSIONS_MESSAGES, type RolePermissionsMessages } from "./messages";
import type {
  PermissionDefinition,
  PermissionRole,
  RolePermissionCustomIdOptions,
  RolePermissionTargetItem,
  RolePermissionTargetLoadResult,
  RolePermissionTargetSection,
  RolePermissionsApi,
  RolePermissionsDraft,
} from "./types";

type PermissionPanelView = "list" | "edit";

const EMPTY_DRAFT: RolePermissionsDraft = { permissions: [], targets: {} };
const NO_TARGETS: never[] = [];

function compareText(left: string, right: string, direction: DataTableSort["direction"]) {
  const result = left.localeCompare(right, "ja");
  return direction === "asc" ? result : -result;
}

function compareNumber(left: number, right: number, direction: DataTableSort["direction"]) {
  const result = left - right;
  return direction === "asc" ? result : -result;
}

function normalizedRole<R extends PermissionRole>(role: R): R {
  return { ...role, permissions: role.permissions ?? [] };
}

/** 対象の表示名。補足があれば「名前 (補足)」。 */
export function targetItemLabel(item: RolePermissionTargetItem) {
  return [item.name, item.secondary ? `(${item.secondary})` : ""].filter(Boolean).join(" ");
}

/**
 * 候補にない ID（直接入力した ID や、候補の取得元に現れなくなった保存済みの ID）を候補の末尾に足す。
 * 名前は ID のまま、`customStatus` があれば状態として添える（#215）。
 */
/**
 * 対象の `load` の結果を、候補と警告にそろえる（#240）。配列は全件読めた結果、
 * `{ items, warning }` は一部だけ読めた結果（読めた候補を出し、警告を表示する）。
 */
export function targetLoadRows(result: RolePermissionTargetItem[] | RolePermissionTargetLoadResult): {
  rows: RolePermissionTargetItem[];
  warning: string;
} {
  if (Array.isArray(result)) return { rows: result, warning: "" };
  return { rows: result.items, warning: result.warning?.trim() ?? "" };
}

export function targetItemsWithCustomIds(
  items: readonly RolePermissionTargetItem[],
  ids: readonly string[],
  customStatus?: string,
): RolePermissionTargetItem[] {
  const known = new Set(items.map((item) => item.id));
  const extra = [...new Set(ids)]
    .filter((id) => !known.has(id))
    .map((id): RolePermissionTargetItem => (customStatus ? { id, name: id, status: customStatus } : { id, name: id }));
  return [...items, ...extra];
}

/** 直接入力した ID を検証する。前後の空白を除いた ID を返し、空・形式に合わなければ null（#215）。 */
export function normalizeCustomTargetId(value: string, pattern?: RegExp): string | null {
  const id = value.trim();
  if (!id) return null;
  if (pattern) {
    // g / y フラグ付きの RegExp でも毎回先頭から判定する。
    pattern.lastIndex = 0;
    if (!pattern.test(id)) return null;
  }
  return id;
}

/** 対象の候補。直接入力を許可する対象では、選択済みで候補にない ID も含める。 */
function targetItemsFor<R extends PermissionRole>(
  target: RolePermissionTargetSection<R>,
  items: readonly RolePermissionTargetItem[],
  selectedIds: readonly string[],
): RolePermissionTargetItem[] {
  return target.allowCustomIds
    ? targetItemsWithCustomIds(items, selectedIds, target.allowCustomIds.customStatus)
    : [...items];
}

/** 直接付けた権限から `implies` で継承される権限と、その継承元の権限名。 */
export function permissionInheritanceSources(
  directCodes: readonly string[],
  permissionByCode: Map<string, PermissionDefinition>,
) {
  const sources = new Map<string, string[]>();
  for (const directCode of directCodes) {
    const source = permissionByCode.get(directCode);
    if (!source) continue;
    const pending = [...source.implies];
    const seen = new Set<string>();
    while (pending.length > 0) {
      const impliedCode = pending.pop();
      if (!impliedCode || seen.has(impliedCode)) continue;
      seen.add(impliedCode);
      const labels = sources.get(impliedCode) ?? [];
      if (!labels.includes(source.label)) labels.push(source.label);
      sources.set(impliedCode, labels);
      pending.push(...(permissionByCode.get(impliedCode)?.implies ?? []));
    }
  }
  return sources;
}

/** 直接付けた権限と、`implies` で継承される権限を合わせた有効な権限コード。 */
export function effectivePermissionCodes(
  directCodes: readonly string[],
  permissionByCode: Map<string, PermissionDefinition>,
) {
  const codes = new Set(directCodes);
  for (const code of permissionInheritanceSources(directCodes, permissionByCode).keys()) {
    codes.add(code);
  }
  return codes;
}

/** SYSTEM_ADMIN、または有効な権限から全件が対象になるか。 */
function targetGrantsAll<R extends PermissionRole>(
  target: RolePermissionTargetSection<R>,
  roleCode: string | undefined,
  effectiveCodes: ReadonlySet<string>,
) {
  return roleCode === SYSTEM_ADMIN_ROLE_CODE || Boolean(target.grantsAll?.(effectiveCodes));
}

/** 組み込みロールとアーカイブ済みロールは権限を変更できない（backend も 409 で拒否する）。 */
function permissionsEditable(role: PermissionRole) {
  return !role.is_built_in && !role.archived;
}

function canonicalDraft(value: RolePermissionsDraft) {
  return JSON.stringify({
    permissions: [...new Set(value.permissions)].sort(),
    targets: Object.fromEntries(
      Object.entries(value.targets)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, ids]) => [key, [...new Set(ids)].sort()]),
    ),
  });
}

export interface RolePermissionsPageProps<R extends PermissionRole = PermissionRole> {
  api: RolePermissionsApi<R>;
  /** 権限を編集できるか（製品の権限判定）。false なら読み取り専用。 */
  canManage: boolean;
  /**
   * ロールに付ける「利用できる対象」（製品固有）。無ければ機能権限だけを扱う。
   * 一覧の検索を毎 render 作り直さないよう、module 定数か useMemo で安定した配列を渡す。
   */
  targets?: RolePermissionTargetSection<R>[];
  /** 一覧と詳細の分割比率を保存する localStorage key の前置き。 */
  splitStoragePrefix?: string;
  /** 既定の文言（日本語）の一部を上書きする。 */
  messages?: Partial<RolePermissionsMessages>;
}

/**
 * 権限管理（NL2SQL から移設。#220）。ロールごとの機能権限と、製品固有の利用できる対象を設定する。
 * ロールの作成・名称変更・アーカイブは共通のロール管理で行う。`?role=<role_id>` で開くとそのロールを選ぶ。
 */
export function RolePermissionsPage<R extends PermissionRole = PermissionRole>({
  api,
  canManage,
  targets = NO_TARGETS,
  splitStoragePrefix,
  messages,
}: RolePermissionsPageProps<R>) {
  const m = { ...ROLE_PERMISSIONS_MESSAGES, ...messages };
  const confirm = useConfirm();
  const [searchParams] = useSearchParams();
  const requestedRoleId = searchParams.get("role");
  const [roles, setRoles] = useState<R[]>([]);
  const [permissions, setPermissions] = useState<PermissionDefinition[]>([]);
  const [targetItems, setTargetItems] = useState<Record<string, RolePermissionTargetItem[]>>({});
  const [targetLoadWarnings, setTargetLoadWarnings] = useState<Record<string, string>>({});
  // ロール管理からの導線（?role=）で開いた場合は、そのロールを利用者が選んだものとして扱う。
  const [selection, setSelection] = useState<{ id: string | null; manual: boolean }>({
    id: requestedRoleId,
    manual: Boolean(requestedRoleId),
  });
  const selectedId = selection.id;
  const selectRole = (id: string | null, manual = true) => setSelection({ id, manual });
  const [editingId, setEditingId] = useState<string | null>(null);
  const [activeView, setActiveView] = useState<PermissionPanelView>("list");
  const [search, setSearch] = useState("");
  const [targetSearch, setTargetSearch] = useState<Record<string, string>>({});
  const [sort, setSort] = useState<DataTableSort>({ key: "role", direction: "asc" });
  const [draft, setDraft] = useState<RolePermissionsDraft>(EMPTY_DRAFT);
  const [baseline, setBaseline] = useState<RolePermissionsDraft>(EMPTY_DRAFT);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [formError, setFormError] = useState("");
  const formRef = useRef<HTMLFormElement | null>(null);
  const loadSequence = useRef(0);
  const { abortAll, run: runScopedRequest } = useRequestScope();

  const editingRole = roles.find((role) => role.role_id === editingId) ?? null;
  const readOnly = Boolean(!canManage || (editingRole && !permissionsEditable(editingRole)));
  const operationBusy = busy || loading;
  const inputReadOnly = readOnly || operationBusy;
  const isDirty = activeView !== "list" && canonicalDraft(draft) !== canonicalDraft(baseline);
  const confirmLeave = async () =>
    !operationBusy &&
    (!isDirty ||
      (await confirm({
        title: m.discardTitle,
        description: m.discardDescription,
        confirmLabel: m.discardConfirm,
        tone: "danger",
        dismissOnOverlay: false,
      })));
  useUnsavedChangesGuard(isDirty || busy, confirmLeave);
  const permissionByCode = useMemo(
    () => new Map(permissions.map((permission) => [permission.code, permission])),
    [permissions],
  );

  const permissionGroups = useMemo(() => {
    const groups = new Map<string, PermissionDefinition[]>();
    for (const permission of permissions) {
      const values = groups.get(permission.group) ?? [];
      values.push(permission);
      groups.set(permission.group, values);
    }
    return [...groups.entries()];
  }, [permissions]);

  const draftInheritedPermissionSources = useMemo(
    () => permissionInheritanceSources(draft.permissions, permissionByCode),
    [draft.permissions, permissionByCode],
  );
  const draftEffectivePermissionCodes = useMemo(
    () => effectivePermissionCodes(draft.permissions, permissionByCode),
    [draft.permissions, permissionByCode],
  );
  const draftGrantsAll = (target: RolePermissionTargetSection<R>) =>
    targetGrantsAll(target, editingRole?.role_code, draftEffectivePermissionCodes);

  // 一覧の検索（useMemo）から使うため、権限定義・対象の候補が変わったときだけ作り直す。
  const roleSearchText = useCallback(
    (role: R) => {
      const effectiveCodes = effectivePermissionCodes(role.permissions, permissionByCode);
      return [
        role.role_code,
        role.display_name,
        ...[...effectiveCodes].map((code) => permissionByCode.get(code)?.label ?? code),
        ...targets.map((target) => {
          if (targetGrantsAll(target, role.role_code, effectiveCodes)) return target.messages.all;
          const selected = target.selectedIds(role);
          return targetItemsFor(target, targetItems[target.key] ?? [], selected)
            .filter((item) => selected.includes(item.id))
            .map(targetItemLabel)
            .join(" ");
        }),
      ]
        .join(" ")
        .toLowerCase();
    },
    [permissionByCode, targetItems, targets],
  );

  const filteredRoles = useMemo(() => {
    const q = search.trim().toLowerCase();
    return roles
      .filter((role) => (q ? roleSearchText(role).includes(q) : true))
      .sort((left, right) => {
        if (sort.key === "permissions") {
          return compareNumber(
            effectivePermissionCodes(left.permissions, permissionByCode).size,
            effectivePermissionCodes(right.permissions, permissionByCode).size,
            sort.direction,
          );
        }
        // 1 列目はロールコードを主表示するため、コードで並べる。
        return compareText(left.role_code, right.role_code, sort.direction);
      });
  }, [permissionByCode, roleSearchText, roles, search, sort]);

  const visibleSelectedId =
    activeView === "list"
      ? selectedVisibleKey(filteredRoles, selectedId, (role) => role.role_id, {
          preserveSelected: selection.manual,
        })
      : selectedId;
  const selectedRole = roles.find((role) => role.role_id === visibleSelectedId) ?? null;

  const load = async (announce = false) => {
    if (busy) return;
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    setLoading(true);
    setLoadError("");
    setTargetLoadWarnings({});
    await requestData(sequence, announce);
  };

  // 読込中・エラー表示の初期化は呼び出し側で行う（初回表示は初期 state が読込中）。
  const requestData = async (sequence: number, announce: boolean) => {
    try {
      await runScopedRequest(async (signal) => {
        // 対象の候補の取得失敗は警告にとどめ、ロール一覧と機能権限は表示する。
        const targetRequests = targets.map((target) =>
          target
            .load({ signal })
            .then((result) => ({ key: target.key, ...targetLoadRows(result) }))
            .catch((cause: unknown) => {
              if (isAbortError(cause)) throw cause;
              const message =
                cause instanceof Error && cause.message.trim() ? cause.message : m.loadError;
              return {
                key: target.key,
                rows: [] as RolePermissionTargetItem[],
                warning: formatMessage(target.messages.loadWarning, { message }),
              };
            }),
        );
        const [roleRows, permissionRows, targetRows] = await Promise.all([
          api.roles(true, { signal }),
          api.permissions({ signal }),
          Promise.all(targetRequests),
        ]);
        if (signal.aborted || sequence !== loadSequence.current) return;
        setRoles(roleRows.map(normalizedRole));
        setPermissions(permissionRows);
        setTargetItems(Object.fromEntries(targetRows.map((row) => [row.key, row.rows])));
        setTargetLoadWarnings(
          Object.fromEntries(targetRows.filter((row) => row.warning).map((row) => [row.key, row.warning])),
        );
        setSelection((current) =>
          !current.id || roleRows.some((role) => role.role_id === current.id)
            ? current
            : { ...current, id: null },
        );
      });
      if (announce && sequence === loadSequence.current) {
        toast.success(m.refreshed);
      }
    } catch (cause) {
      if (isAbortError(cause)) return;
      const nextError = cause instanceof Error && cause.message.trim() ? cause.message : m.loadError;
      if (sequence === loadSequence.current) setLoadError(nextError);
    } finally {
      if (sequence === loadSequence.current) setLoading(false);
    }
  };

  // 初回ロードは mount 時だけ行う。最新の requestData を commit 時に ref へ入れて呼ぶ。
  const requestDataRef = useRef(requestData);
  useLayoutEffect(() => {
    requestDataRef.current = requestData;
  });
  useEffect(() => {
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    void requestDataRef.current(sequence, false);
    return () => {
      loadSequence.current += 1;
      abortAll();
    };
  }, [abortAll]);

  // 一覧の表示内容が変わったら、見えている行へ選択を render 中に合わせる。
  if (useValuesChanged([activeView, filteredRoles, loading]) && activeView === "list" && !loading) {
    const nextId = selectedVisibleKey(filteredRoles, selection.id, (role) => role.role_id, {
      preserveSelected: selection.manual,
    });
    if (nextId !== selection.id) setSelection({ id: nextId, manual: false });
  }

  const startEdit = (role: R) => {
    selectRole(role.role_id);
    setEditingId(role.role_id);
    setActiveView("edit");
    const nextDraft: RolePermissionsDraft = {
      permissions: role.permissions,
      targets: Object.fromEntries(targets.map((target) => [target.key, target.selectedIds(role)])),
    };
    setDraft(nextDraft);
    setBaseline(nextDraft);
    setTargetSearch({});
    setFormError("");
  };

  const finishToList = () => {
    setDraft(EMPTY_DRAFT);
    setBaseline(EMPTY_DRAFT);
    setActiveView("list");
    setEditingId(null);
    setFormError("");
  };

  const returnToList = async () => {
    if (await confirmLeave()) finishToList();
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (inputReadOnly || !editingRole) return;
    setBusy(true);
    setFormError("");
    try {
      // 全件が対象のときは個別の ID を保存しない（空の一覧を送る）。
      const updated = normalizedRole(
        await api.save(editingRole, {
          permissions: draft.permissions,
          targets: Object.fromEntries(
            targets.map((target) => [
              target.key,
              draftGrantsAll(target) ? [] : (draft.targets[target.key] ?? []),
            ]),
          ),
        }),
      );
      setRoles((rows) => rows.map((row) => (row.role_id === updated.role_id ? updated : row)));
      startEdit(updated);
      toast.success(m.saved);
    } catch (cause) {
      setFormError(cause instanceof Error && cause.message.trim() ? cause.message : m.saveError);
    } finally {
      setBusy(false);
    }
  };

  const roleActions = (role: R): EntityAction[] =>
    canManage && permissionsEditable(role)
      ? [
          {
            id: "edit-permissions",
            label: m.edit,
            icon: Pencil,
            disabled: operationBusy,
            onSelect: () => {
              if (!operationBusy) startEdit(role);
            },
          },
        ]
      : [];

  const togglePermission = (code: string) => {
    if (inputReadOnly) return;
    setDraft((current) => ({
      ...current,
      permissions: current.permissions.includes(code)
        ? current.permissions.filter((value) => value !== code)
        : [...current.permissions, code],
    }));
  };
  const permissionCodes = permissions.map((permission) => permission.code);
  const selectedPermissionCount = permissionCodes.filter((code) => draft.permissions.includes(code)).length;
  const selectPermissions = (codes: string[]) => {
    if (inputReadOnly) return;
    setDraft((current) => ({
      ...current,
      permissions: [...new Set([...current.permissions, ...codes])],
    }));
  };
  const clearPermissions = (codes: string[]) => {
    if (inputReadOnly) return;
    const codeSet = new Set(codes);
    setDraft((current) => ({
      ...current,
      permissions: current.permissions.filter((code) => !codeSet.has(code)),
    }));
  };
  const setTargetIds = (key: string, update: (ids: string[]) => string[]) =>
    setDraft((current) => ({
      ...current,
      targets: { ...current.targets, [key]: update(current.targets[key] ?? []) },
    }));

  const roleColumns: Array<DataTableColumn<R>> = [
    {
      key: "role",
      header: m.columnRole,
      sortable: true,
      className: "min-w-52 align-top",
      render: (role) => {
        const selected = visibleSelectedId === role.role_id;
        return (
          <button
            type="button"
            className={`min-w-0 cursor-pointer text-left focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring ${
              selected ? "text-accent-fg" : "text-fg"
            }`}
            aria-label={formatMessage(m.showRole, { name: role.role_code })}
            aria-current={selected ? "true" : undefined}
            onClick={(event) => {
              event.stopPropagation();
              if (operationBusy) return;
              selectRole(role.role_id);
            }}
          >
            <SecurityIdentityLines id={role.role_code} name={role.display_name} />
          </button>
        );
      },
    },
    {
      key: "status",
      header: m.status,
      className: "min-w-32 align-top",
      render: (role) => <RoleStatusBadges role={role} />,
    },
    {
      key: "permissions",
      header: m.permissions,
      sortable: true,
      className: "min-w-28 align-top",
      render: (role) =>
        role.role_code === SYSTEM_ADMIN_ROLE_CODE
          ? m.allPermissions
          : formatMessage(m.permissionCount, {
              count: effectivePermissionCodes(role.permissions, permissionByCode).size,
            }),
    },
    ...targets.map<DataTableColumn<R>>((target) => ({
      key: `target-${target.key}`,
      header: target.messages.title,
      className: "min-w-32 align-top",
      render: (role) =>
        targetGrantsAll(target, role.role_code, effectivePermissionCodes(role.permissions, permissionByCode))
          ? target.messages.all
          : formatMessage(target.messages.count ?? m.permissionCount, {
              count: target.selectedIds(role).length,
            }),
    })),
  ];

  return (
    <>
      <PageHeader
        wide
        title={m.title}
        subtitle={m.subtitle}
        actions={
          activeView === "list"
            ? [
                {
                  id: "refresh",
                  kind: "utility",
                  label: m.refresh,
                  icon: RefreshCw,
                  disabled: operationBusy,
                  onClick: () => load(true),
                  loading,
                },
              ]
            : []
        }
        actionsLabel={m.actionsLabel}
        actionsTestId="security-permissions-actions"
      />
      <PageBody wide className="grid gap-4">
        {loadError ? <Banner severity="danger">{loadError}</Banner> : null}
        {targets.map((target) =>
          targetLoadWarnings[target.key] ? (
            <Banner key={target.key} severity="warning">
              {targetLoadWarnings[target.key]}
            </Banner>
          ) : null,
        )}

        {activeView === "list" ? (
          <SecurityManagementPanelShell
            id="security-permissions-panel-list"
            idPrefix="security-permissions"
            ariaLabel={m.workspaceLabel}
            splitId="security-permissions-list"
            splitStoragePrefix={splitStoragePrefix}
            preferredWidePane="right"
          >
            <section className="grid min-w-0 content-start gap-3" aria-labelledby="security-permissions-list-heading">
              <SecurityPanelHeader
                headingId="security-permissions-list-heading"
                icon={LockKeyhole}
                title={m.listTitle}
                description={m.listHint}
                action={
                  <StatusBadge
                    icon={false}
                    variant="info"
                    label={securityFilteredCount(filteredRoles.length, roles.length)}
                  />
                }
              />
              <div className="rounded-md border border-border bg-surface-sunken p-3">
                <SecuritySearchField
                  label={m.search}
                  placeholder={m.searchPlaceholder}
                  value={search}
                  testId="security-permissions-search"
                  disabled={operationBusy}
                  onChange={setSearch}
                />
              </div>
              {loading ? (
                <ProcessingIndicator
                  active
                  label={m.loading}
                  operationKey="security-permissions-load"
                  placement="panel"
                  testId="security-permissions-loading"
                  activityIcon="none"
                />
              ) : null}
              <DataTable
                dense
                loading={loading}
                rows={filteredRoles}
                sort={sort}
                onSortChange={(next) => {
                  if (!operationBusy) setSort(next);
                }}
                selectedRowKey={visibleSelectedId}
                onRowClick={(role) => {
                  if (operationBusy) return;
                  selectRole(role.role_id);
                }}
                getRowKey={(role) => role.role_id}
                rowProps={(role) => ({
                  className: INFORMATION_TABLE_ROW_CLASS,
                  "aria-label": formatMessage(m.showRole, { name: role.role_code }),
                })}
                ariaLabel={m.listTitle}
                testId="security-permissions-grid"
                scrollAriaLabel={formatMessage(m.listScrollLabel, { list: m.listTitle })}
                scrollTestId="security-permissions-scroll-region"
                stickyHeader
                visibleRows={INFORMATION_TABLE_VISIBLE_ROWS}
                empty={
                  <EmptyState
                    title={search ? m.noResultsTitle : m.empty}
                    hint={search ? m.noResultsHint : undefined}
                  />
                }
                columns={roleColumns}
              />
            </section>

            <PermissionDetailPanel
              role={selectedRole}
              canManage={canManage}
              permissionByCode={permissionByCode}
              targets={targets}
              targetItems={targetItems}
              actions={selectedRole ? roleActions(selectedRole) : []}
              messages={m}
            />
          </SecurityManagementPanelShell>
        ) : (
          <>
            <div>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={operationBusy}
                onClick={returnToList}
                icon={ArrowLeft}
              >
                <span>{m.backToList}</span>
              </Button>
            </div>
            <SecurityManagementPanelShell
              id="security-permissions-panel-edit"
              idPrefix="security-permissions"
              ariaLabel={m.taskPanelLabel}
            >
              <SecurityPanelHeader
                icon={Pencil}
                title={formatMessage(m.formTitle, { role: editingRole?.role_code ?? "" })}
                description={m.formHint}
                headingId="security-permissions-form-heading"
              />
              <form
                ref={formRef}
                className="grid gap-6"
                onSubmit={handleSubmit}
                aria-labelledby="security-permissions-form-heading"
              >
                {editingRole ? (
                  <dl className="grid gap-3 md:grid-cols-2">
                    <SecurityDetailField label={m.roleCode}>
                      <code className="break-all font-mono text-xs">{editingRole.role_code}</code>
                    </SecurityDetailField>
                    <SecurityDetailField label={m.roleName}>{editingRole.display_name}</SecurityDetailField>
                  </dl>
                ) : null}

                <fieldset className="grid gap-3" disabled={inputReadOnly}>
                  <legend className="text-base font-semibold">{m.permissions}</legend>
                  <p className="text-sm text-fg-muted">{m.permissionsHint}</p>
                  {permissions.length > 0 ? (
                    <BulkSelectionActions
                      selectLabel={m.selectAll}
                      clearLabel={m.clearAll}
                      selectDisabled={readOnly || selectedPermissionCount === permissionCodes.length}
                      clearDisabled={readOnly || selectedPermissionCount === 0}
                      dataTestId="security-roles-permission-selection-actions"
                      onSelectAll={() => selectPermissions(permissionCodes)}
                      onClearAll={() => clearPermissions(permissionCodes)}
                    />
                  ) : null}
                  {permissionGroups.length === 0 ? (
                    <p className="rounded-md border border-dashed border-border p-4 text-sm text-fg-muted">
                      {m.empty}
                    </p>
                  ) : (
                    <div className="grid gap-3 lg:grid-cols-2">
                      {permissionGroups.map(([group, groupPermissions]) => {
                        const groupCodes = groupPermissions.map((permission) => permission.code);
                        const selectedGroupCount = groupCodes.filter((code) =>
                          draft.permissions.includes(code),
                        ).length;
                        return (
                          <div key={group} className="rounded-md border border-border p-3">
                            <div className="mb-2 grid gap-2">
                              <h3 className="text-sm font-semibold">{group}</h3>
                              <BulkSelectionActions
                                selectLabel={m.selectAll}
                                clearLabel={m.clearAll}
                                selectAriaLabel={formatMessage(m.selectGroup, { name: group })}
                                clearAriaLabel={formatMessage(m.clearGroup, { name: group })}
                                selectDisabled={readOnly || selectedGroupCount === groupCodes.length}
                                clearDisabled={readOnly || selectedGroupCount === 0}
                                dataTestId={`security-roles-${group}-permission-selection-actions`}
                                onSelectAll={() => selectPermissions(groupCodes)}
                                onClearAll={() => clearPermissions(groupCodes)}
                              />
                            </div>
                            <div className="grid gap-2">
                              {groupPermissions.map((permission) => {
                                const checkedDirect = draft.permissions.includes(permission.code);
                                const inheritedSources = draftInheritedPermissionSources.get(permission.code) ?? [];
                                const inherited = !checkedDirect && inheritedSources.length > 0;
                                return (
                                  <label
                                    key={permission.code}
                                    className={`flex min-h-11 items-start gap-2 text-sm ${
                                      readOnly || inherited ? "cursor-not-allowed opacity-80" : "cursor-pointer"
                                    }`}
                                  >
                                    <input
                                      className="mt-0.5 h-4 w-4 accent-accent-emphasis disabled:cursor-not-allowed"
                                      type="checkbox"
                                      checked={checkedDirect || inherited}
                                      disabled={inputReadOnly || inherited}
                                      onChange={() => {
                                        if (!inherited) togglePermission(permission.code);
                                      }}
                                    />
                                    <span className="min-w-0">
                                      <span className="flex flex-wrap items-center gap-1.5 font-medium">
                                        <span>{permission.label}</span>
                                        {inherited ? (
                                          <StatusBadge
                                            icon={false}
                                            variant="neutral"
                                            label={formatMessage(m.permissionInherited, {
                                              source: inheritedSources[0],
                                            })}
                                          />
                                        ) : null}
                                      </span>
                                      <span className="block text-xs leading-5 text-fg-muted">
                                        {permission.description}
                                      </span>
                                      <code className="block break-all text-xs text-fg-muted">{permission.code}</code>
                                    </span>
                                  </label>
                                );
                              })}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </fieldset>

                {targets.map((target) => (
                  <TargetFieldset
                    key={target.key}
                    target={target}
                    items={targetItems[target.key] ?? []}
                    selectedIds={draft.targets[target.key] ?? []}
                    search={targetSearch[target.key] ?? ""}
                    grantsAll={draftGrantsAll(target)}
                    systemAdmin={editingRole?.role_code === SYSTEM_ADMIN_ROLE_CODE}
                    inputReadOnly={inputReadOnly}
                    messages={m}
                    onSearchChange={(value) => setTargetSearch((current) => ({ ...current, [target.key]: value }))}
                    onChange={(update) => setTargetIds(target.key, update)}
                  />
                ))}

                <FormActionBar
                  ariaLabel={m.editActions}
                  testId="security-permissions-form-actions"
                  primaryActions={
                    !readOnly
                      ? [
                          {
                            id: "save",
                            label: m.save,
                            loading: busy,
                            disabled: operationBusy,
                            onClick: () => {
                              formRef.current?.requestSubmit();
                            },
                          },
                        ]
                      : []
                  }
                  secondaryActions={[
                    {
                      id: "cancel",
                      label: m.cancel,
                      disabled: operationBusy,
                      onClick: returnToList,
                    },
                  ]}
                  status={<FormStatus tone="danger" message={formError} />}
                />
              </form>
            </SecurityManagementPanelShell>
          </>
        )}
      </PageBody>
    </>
  );
}

/**
 * 利用できる対象 1 種類分の選択欄。全件が対象（SYSTEM_ADMIN・管理権限）のときは説明だけを出す。
 * 候補は検索でき、表示中の候補を一括で選択・解除できる。
 */
function TargetFieldset<R extends PermissionRole>({
  target,
  items,
  selectedIds,
  search,
  grantsAll,
  systemAdmin,
  inputReadOnly,
  messages: m,
  onSearchChange,
  onChange,
}: {
  target: RolePermissionTargetSection<R>;
  items: RolePermissionTargetItem[];
  selectedIds: string[];
  search: string;
  grantsAll: boolean;
  systemAdmin: boolean;
  inputReadOnly: boolean;
  messages: RolePermissionsMessages;
  onSearchChange: (value: string) => void;
  onChange: (update: (ids: string[]) => string[]) => void;
}) {
  const tm = target.messages;
  const idPrefix = `security-roles-${target.key}`;
  const targetReadOnly = inputReadOnly || grantsAll;
  const custom = target.allowCustomIds;
  // 直接入力で足した ID は、選択を外しても編集中は候補に残す（選び直せるように）。
  const [addedIds, setAddedIds] = useState<string[]>([]);
  const displayItems = custom
    ? targetItemsWithCustomIds(items, [...selectedIds, ...addedIds], custom.customStatus)
    : items;
  const q = search.trim().toLowerCase();
  const filteredItems = q
    ? displayItems.filter((item) =>
        [item.id, item.name, item.secondary ?? "", item.description ?? ""].join(" ").toLowerCase().includes(q),
      )
    : displayItems;
  const visibleIds = filteredItems.map((item) => item.id);
  const selectedVisibleCount = visibleIds.filter((id) => selectedIds.includes(id)).length;
  const toggle = (id: string) => {
    if (targetReadOnly) return;
    onChange((ids) => (ids.includes(id) ? ids.filter((value) => value !== id) : [...ids, id]));
  };
  const selectVisible = () => {
    if (targetReadOnly) return;
    onChange((ids) => [...new Set([...ids, ...visibleIds])]);
  };
  const clearVisible = () => {
    if (targetReadOnly) return;
    const visible = new Set(visibleIds);
    onChange((ids) => ids.filter((id) => !visible.has(id)));
  };

  return (
    <fieldset className="grid gap-3" disabled={targetReadOnly}>
      <legend id={`${idPrefix}-label`} className="text-base font-semibold">
        {tm.title}
      </legend>
      {tm.hint ? <p className="text-sm text-fg-muted">{tm.hint}</p> : null}
      {grantsAll ? (
        <Banner severity="info">{systemAdmin ? tm.grantsAllSystemAdmin : tm.grantsAllByPermission}</Banner>
      ) : (
        <>
          <div className="rounded-md border border-border bg-surface-sunken p-3">
            <SecuritySearchField
              label={tm.searchLabel}
              placeholder={tm.searchPlaceholder}
              value={search}
              testId={`${idPrefix}-search`}
              disabled={targetReadOnly}
              onChange={(value) => {
                if (targetReadOnly) return;
                onSearchChange(value);
              }}
            />
          </div>
          {custom ? (
            <CustomTargetIdField
              idPrefix={idPrefix}
              options={custom}
              disabled={targetReadOnly}
              onAdd={(id) => {
                setAddedIds((current) => (current.includes(id) ? current : [...current, id]));
                onChange((ids) => (ids.includes(id) ? ids : [...ids, id]));
                // 追加した ID が検索で隠れないよう、検索語を消す。
                if (search) onSearchChange("");
              }}
            />
          ) : null}
          {displayItems.length > 0 ? (
            <BulkSelectionActions
              selectLabel={m.selectAll}
              clearLabel={m.clearAll}
              selectDisabled={
                targetReadOnly || visibleIds.length === 0 || selectedVisibleCount === visibleIds.length
              }
              clearDisabled={targetReadOnly || selectedVisibleCount === 0}
              dataTestId={`${idPrefix}-selection-actions`}
              onSelectAll={selectVisible}
              onClearAll={clearVisible}
            />
          ) : null}
          {displayItems.length === 0 ? (
            <p className="rounded-md border border-dashed border-border p-4 text-sm text-fg-muted">{tm.empty}</p>
          ) : filteredItems.length === 0 ? (
            <p className="rounded-md border border-dashed border-border p-4 text-sm text-fg-muted">{tm.noResults}</p>
          ) : (
            <div
              role="region"
              aria-labelledby={`${idPrefix}-label`}
              tabIndex={0}
              data-testid={`${idPrefix}-list`}
              className={`grid min-w-0 gap-2 overflow-x-hidden rounded-md border border-border bg-surface-sunken p-3 pr-4 lg:grid-cols-2 ${INFORMATION_LIST_SCROLL_CLASS} ${INFORMATION_TABLE_FOCUS_CLASS}`}
            >
              {filteredItems.map((item) => (
                <label
                  key={item.id}
                  className={`flex min-h-11 items-start gap-2 text-sm ${
                    targetReadOnly ? "cursor-not-allowed opacity-80" : "cursor-pointer"
                  }`}
                >
                  <input
                    className="mt-0.5 h-4 w-4 accent-accent-emphasis disabled:cursor-not-allowed"
                    type="checkbox"
                    checked={selectedIds.includes(item.id)}
                    disabled={targetReadOnly}
                    onChange={() => toggle(item.id)}
                  />
                  <span className="min-w-0">
                    <span className="flex flex-wrap items-center gap-1.5 font-medium">
                      <span>{targetItemLabel(item)}</span>
                      {item.status ? <StatusBadge icon={false} variant="neutral" label={item.status} /> : null}
                    </span>
                    {item.description ? (
                      <span className="block text-xs leading-5 text-fg-muted">{item.description}</span>
                    ) : null}
                    <code className="block break-all text-xs text-fg-muted">{item.id}</code>
                  </span>
                </label>
              ))}
            </div>
          )}
        </>
      )}
    </fieldset>
  );
}

/**
 * 候補にない ID を直接入力して追加する欄（#215）。Enter でも追加し、フォームは送信しない。
 * 空・形式に合わない ID は追加せず、入力欄の直下に理由を出す。
 */
function CustomTargetIdField({
  idPrefix,
  options,
  disabled,
  onAdd,
}: {
  idPrefix: string;
  options: RolePermissionCustomIdOptions;
  disabled: boolean;
  onAdd: (id: string) => void;
}) {
  const [value, setValue] = useState("");
  const [error, setError] = useState("");
  const add = () => {
    if (disabled) return;
    const id = normalizeCustomTargetId(value, options.pattern);
    if (!id) {
      setError(options.invalidMessage);
      return;
    }
    onAdd(id);
    setValue("");
    setError("");
  };
  const hintId = `${idPrefix}-custom-id-hint`;
  const errorId = `${idPrefix}-custom-id-error`;
  const describedBy = [options.hint ? hintId : "", error ? errorId : ""].filter(Boolean).join(" ") || undefined;
  return (
    <div className="grid gap-1.5 rounded-md border border-border bg-surface-sunken p-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <TextField
          id={`${idPrefix}-custom-id`}
          className="min-w-0 flex-1"
          label={options.label}
          placeholder={options.placeholder}
          value={value}
          disabled={disabled}
          autoComplete="off"
          spellCheck={false}
          data-testid={`${idPrefix}-custom-id`}
          // 補足とエラーは追加ボタンの行の下に出すため、入力欄との関連付けをここで持つ。
          aria-invalid={Boolean(error)}
          aria-describedby={describedBy}
          inputClassName={error ? "border-danger-fg" : undefined}
          onValueChange={(next) => {
            setValue(next);
            if (error) setError("");
          }}
          onKeyDown={(event) => {
            if (event.key !== "Enter" || event.nativeEvent.isComposing) return;
            event.preventDefault();
            add();
          }}
        />
        <Button
          type="button"
          variant="secondary"
          icon={Plus}
          disabled={disabled}
          onClick={add}
          data-testid={`${idPrefix}-custom-add`}
        >
          {options.addLabel}
        </Button>
      </div>
      {options.hint ? (
        <p id={hintId} className="text-xs leading-relaxed text-fg-muted">
          {options.hint}
        </p>
      ) : null}
      <FieldError id={errorId} message={error} />
    </div>
  );
}

function PermissionDetailPanel<R extends PermissionRole>({
  role,
  canManage,
  permissionByCode,
  targets,
  targetItems,
  actions,
  messages: m,
}: {
  role: R | null;
  canManage: boolean;
  permissionByCode: Map<string, PermissionDefinition>;
  targets: RolePermissionTargetSection<R>[];
  targetItems: Record<string, RolePermissionTargetItem[]>;
  actions: EntityAction[];
  messages: RolePermissionsMessages;
}) {
  if (!role) {
    return <SecurityEmptySelection title={m.noSelectionTitle} hint={m.noSelectionHint} />;
  }

  const effectiveCodes = effectivePermissionCodes(role.permissions, permissionByCode);
  const targetSummaries = targets.map((target) => {
    const grantsAll = targetGrantsAll(target, role.role_code, effectiveCodes);
    const selected = target.selectedIds(role);
    const items = grantsAll ? (targetItems[target.key] ?? []) : targetItemsFor(target, targetItems[target.key] ?? [], selected);
    return { target, grantsAll, items: grantsAll ? items : items.filter((item) => selected.includes(item.id)) };
  });

  return (
    <section
      className="grid min-w-0 content-start gap-4 rounded-md border border-border bg-surface-sunken p-4"
      aria-labelledby="security-permissions-detail-heading"
    >
      <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2
              id="security-permissions-detail-heading"
              className="flex min-w-0 items-center gap-2 text-base font-semibold text-fg"
            >
              <ShieldCheck size={20} aria-hidden="true" />
              <span className="min-w-0 break-all font-mono">{role.role_code}</span>
            </h2>
            <RoleStatusBadges role={role} />
          </div>
          {identitySecondaryName(role.role_code, role.display_name) ? (
            <p className="mt-1 break-words text-xs text-fg-muted">{role.display_name}</p>
          ) : null}
        </div>
        {canManage && actions.length > 0 ? (
          <ObjectActionBar
            actions={actions}
            ariaLabel={`${m.actions}: ${role.role_code}`}
            testId="security-permissions-detail-actions"
          />
        ) : null}
      </div>

      {role.role_code === SYSTEM_ADMIN_ROLE_CODE ? (
        <Banner severity="info">{m.systemAdminNotice}</Banner>
      ) : role.archived ? (
        <Banner severity="warning">{m.archivedPermissionNotice}</Banner>
      ) : null}

      <dl className="grid gap-3 md:grid-cols-2">
        <SecurityDetailField label={m.permissions}>
          {role.role_code === SYSTEM_ADMIN_ROLE_CODE
            ? m.allPermissions
            : formatMessage(m.permissionCount, { count: effectiveCodes.size })}
        </SecurityDetailField>
        {targetSummaries.map(({ target, grantsAll, items }) => (
          <SecurityDetailField key={target.key} label={target.messages.title}>
            {grantsAll
              ? target.messages.all
              : formatMessage(target.messages.count ?? m.permissionCount, { count: items.length })}
          </SecurityDetailField>
        ))}
      </dl>
      {targetSummaries.map(({ target, grantsAll, items }) =>
        !grantsAll && items.length > 0 ? (
          <div key={target.key} className="grid gap-2 rounded-md border border-border bg-surface p-3">
            <h3 className="text-sm font-semibold text-fg">{target.messages.title}</h3>
            <div className="flex flex-wrap gap-1.5">
              {items.map((item) => (
                <StatusBadge icon={false} key={item.id} variant="neutral" label={targetItemLabel(item)} />
              ))}
            </div>
          </div>
        ) : null,
      )}
    </section>
  );
}
