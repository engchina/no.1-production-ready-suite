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

import { ArrowLeft, LockKeyhole, Pencil, RefreshCw, ShieldCheck } from "lucide-react";

import {
  Banner,
  EmptyState,
  FormStatus,
  toast,
  DataTable,
  type DataTableColumn,
  type DataTableSort,
  Button,
  StatusBadge,
  PageHeader,
  PageBody,
  useConfirm,
  BulkSelectionActions,
  ProcessingIndicator,
  ObjectActionBar,
  type EntityAction,
} from "@engchina/production-ready-ui";
import {
  FormActionBar,
  RoleStatusBadges,
  SECURITY_LIST_FOCUS_CLASS,
  SECURITY_LIST_SCROLL_CLASS,
  SECURITY_TABLE_ROW_CLASS,
  SECURITY_TABLE_VISIBLE_ROWS,
  SYSTEM_ADMIN_ROLE_CODE,
  SecurityDetailField,
  SecurityEmptySelection,
  SecurityIdentityLines,
  SecurityManagementPanelShell,
  SecurityPanelHeader,
  SecuritySearchField,
  identitySecondaryName,
  securityFilteredCount,
} from "@engchina/production-ready-system-settings";

import { isAbortError } from "@/lib/api";
import { t } from "@/lib/i18n";
import { useValuesChanged } from "@/lib/render-sync";
import { FIXED_SPLIT_STORAGE_PREFIX } from "@/lib/ui-store";
import { useUnsavedChangesGuard } from "@/lib/useUnsavedChangesGuard";
import { useRequestScope } from "@/lib/useRequestScope";
import { selectedVisibleKey } from "@/lib/visible-selection";
import { useAuth } from "./AuthProvider";
import { securityApi } from "./api";
import { MENU_PERMISSIONS } from "./menu-permissions";
import type { PermissionDefinition, ProfileAccessProfile, SecurityRole } from "./types";

type PermissionPanelView = "list" | "edit";

interface PermissionDraftState {
  permissions: string[];
  allowedProfileIds: string[];
}

const PROFILE_MANAGE_PERMISSION = "nl2sql.profiles.manage";
const EMPTY_DRAFT: PermissionDraftState = { permissions: [], allowedProfileIds: [] };

function compareText(left: string, right: string, direction: DataTableSort["direction"]) {
  const result = left.localeCompare(right, "ja");
  return direction === "asc" ? result : -result;
}

function compareNumber(left: number, right: number, direction: DataTableSort["direction"]) {
  const result = left - right;
  return direction === "asc" ? result : -result;
}

function normalizedRole(role: SecurityRole): SecurityRole {
  return {
    ...role,
    allowed_profile_ids: role.allowed_profile_ids ?? [],
    data_entitlements: role.data_entitlements ?? [],
    permissions: role.permissions ?? [],
  };
}

function profileAccessLabel(profile: ProfileAccessProfile) {
  return [profile.name, profile.category ? `(${profile.category})` : ""].filter(Boolean).join(" ");
}

function permissionInheritanceSources(
  directCodes: readonly string[],
  permissionByCode: Map<string, PermissionDefinition>
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

function effectivePermissionCodes(
  directCodes: readonly string[],
  permissionByCode: Map<string, PermissionDefinition>
) {
  const codes = new Set(directCodes);
  for (const code of permissionInheritanceSources(directCodes, permissionByCode).keys()) {
    codes.add(code);
  }
  return codes;
}

function roleGrantsAllProfileAccess(
  role: SecurityRole,
  permissionByCode: Map<string, PermissionDefinition>
) {
  return (
    role.role_code === SYSTEM_ADMIN_ROLE_CODE ||
    effectivePermissionCodes(role.permissions, permissionByCode).has(PROFILE_MANAGE_PERMISSION)
  );
}

/** 組み込みロールとアーカイブ済みロールは権限を変更できない（backend も 409 で拒否する）。 */
function permissionsEditable(role: SecurityRole) {
  return !role.is_built_in && !role.archived;
}

/**
 * 権限管理（NL2SQL 固有。#206）。ロールごとの機能権限と業務プロファイル利用権限を設定する。
 * ロールの作成・名称変更・アーカイブは共通のロール管理で行う。
 */
export function SecurityPermissionsPage() {
  const confirm = useConfirm();
  const { hasPermission } = useAuth();
  const canManage = hasPermission(MENU_PERMISSIONS.securityPermissions);
  const [searchParams] = useSearchParams();
  const requestedRoleId = searchParams.get("role");
  const [roles, setRoles] = useState<SecurityRole[]>([]);
  const [permissions, setPermissions] = useState<PermissionDefinition[]>([]);
  const [profileAccessProfiles, setProfileAccessProfiles] = useState<ProfileAccessProfile[]>([]);
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
  const [profileAccessSearch, setProfileAccessSearch] = useState("");
  const [sort, setSort] = useState<DataTableSort>({ key: "role", direction: "asc" });
  const [draft, setDraft] = useState<PermissionDraftState>(EMPTY_DRAFT);
  const [baseline, setBaseline] = useState<PermissionDraftState>(EMPTY_DRAFT);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [formError, setFormError] = useState("");
  const [profileAccessLoadWarning, setProfileAccessLoadWarning] = useState("");
  const formRef = useRef<HTMLFormElement | null>(null);
  const loadSequence = useRef(0);
  const { abortAll, run: runScopedRequest } = useRequestScope();

  const editingRole = roles.find((role) => role.role_id === editingId) ?? null;
  const readOnly = Boolean(!canManage || (editingRole && !permissionsEditable(editingRole)));
  const operationBusy = busy || loading;
  const inputReadOnly = readOnly || operationBusy;
  const canonicalDraft = (value: PermissionDraftState) =>
    JSON.stringify({
      permissions: [...new Set(value.permissions)].sort(),
      allowedProfileIds: [...new Set(value.allowedProfileIds)].sort(),
    });
  const isDirty = activeView !== "list" && canonicalDraft(draft) !== canonicalDraft(baseline);
  const confirmLeave = async () =>
    !operationBusy &&
    (!isDirty ||
      (await confirm({
        title: t("security.common.discardTitle"),
        description: t("security.common.discardDescription"),
        confirmLabel: t("security.common.discardConfirm"),
        tone: "danger",
        dismissOnOverlay: false,
      })));
  useUnsavedChangesGuard(isDirty || busy, confirmLeave);
  const permissionByCode = useMemo(
    () => new Map(permissions.map((permission) => [permission.code, permission])),
    [permissions]
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
    [draft.permissions, permissionByCode]
  );
  const draftEffectivePermissionCodes = useMemo(
    () => effectivePermissionCodes(draft.permissions, permissionByCode),
    [draft.permissions, permissionByCode]
  );
  const draftGrantsAllProfileAccess =
    editingRole?.role_code === SYSTEM_ADMIN_ROLE_CODE ||
    draftEffectivePermissionCodes.has(PROFILE_MANAGE_PERMISSION);
  const profileAccessReadOnly = inputReadOnly || draftGrantsAllProfileAccess;

  // 一覧の検索（useMemo）から使うため、権限定義・Profile 一覧が変わったときだけ作り直す。
  const roleSearchText = useCallback(
    (role: SecurityRole) =>
      [
        role.role_code,
        role.display_name,
        ...[...effectivePermissionCodes(role.permissions, permissionByCode)].map(
          (code) => permissionByCode.get(code)?.label ?? code
        ),
        roleGrantsAllProfileAccess(role, permissionByCode)
          ? t("security.roles.profileAccessAll")
          : profileAccessProfiles
              .filter((profile) => role.allowed_profile_ids.includes(profile.id))
              .map(profileAccessLabel)
              .join(" "),
      ]
        .join(" ")
        .toLowerCase(),
    [permissionByCode, profileAccessProfiles]
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
            sort.direction
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
    setProfileAccessLoadWarning("");
    await requestData(sequence, announce);
  };

  // 読込中・エラー表示の初期化は呼び出し側で行う（初回表示は初期 state が読込中）。
  const requestData = async (sequence: number, announce: boolean) => {
    try {
      await runScopedRequest(async (signal) => {
        const profileRowsRequest = securityApi
          .profileAccessProfiles({ signal })
          .then((rows) => ({ rows, warning: "" }))
          .catch((cause) => {
            if (isAbortError(cause)) throw cause;
            const message =
              cause instanceof Error && cause.message.trim()
                ? cause.message
                : t("security.common.loadError");
            return {
              rows: [] as ProfileAccessProfile[],
              warning: t("security.roles.profileAccessLoadWarning", { message }),
            };
          });
        const [roleRows, permissionRows, profileRows] = await Promise.all([
          securityApi.roles(true, { signal }),
          securityApi.permissions({ signal }),
          profileRowsRequest,
        ]);
        if (signal.aborted || sequence !== loadSequence.current) return;
        setRoles(roleRows.map(normalizedRole));
        setPermissions(permissionRows);
        setProfileAccessProfiles(profileRows.rows);
        setProfileAccessLoadWarning(profileRows.warning);
        setSelection((current) =>
          !current.id || roleRows.some((role) => role.role_id === current.id)
            ? current
            : { ...current, id: null }
        );
      });
      if (announce && sequence === loadSequence.current) {
        toast.success(t("common.action.refreshed"));
      }
    } catch (cause) {
      if (isAbortError(cause)) return;
      const nextError =
        cause instanceof Error && cause.message.trim()
          ? cause.message
          : t("security.common.loadError");
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

  const startEdit = (role: SecurityRole) => {
    selectRole(role.role_id);
    setEditingId(role.role_id);
    setActiveView("edit");
    const nextDraft = {
      permissions: role.permissions,
      allowedProfileIds: role.allowed_profile_ids,
    };
    setDraft(nextDraft);
    setBaseline(nextDraft);
    setProfileAccessSearch("");
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
      const updated = normalizedRole(
        await securityApi.updateRolePermissions({
          role_id: editingRole.role_id,
          version: editingRole.version,
          permissions: draft.permissions,
          allowed_profile_ids: draftGrantsAllProfileAccess ? [] : draft.allowedProfileIds,
        })
      );
      setRoles((rows) => rows.map((row) => (row.role_id === updated.role_id ? updated : row)));
      startEdit(updated);
      toast.success(t("security.common.saved"));
    } catch (cause) {
      setFormError(
        cause instanceof Error && cause.message.trim()
          ? cause.message
          : t("security.common.saveError")
      );
    } finally {
      setBusy(false);
    }
  };

  const roleActions = (role: SecurityRole): EntityAction[] =>
    canManage && permissionsEditable(role)
      ? [
          {
            id: "edit-permissions",
            label: t("security.permissions.edit"),
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
  const selectedPermissionCount = permissionCodes.filter((code) =>
    draft.permissions.includes(code)
  ).length;
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

  const filteredProfileAccessProfiles = useMemo(() => {
    const q = profileAccessSearch.trim().toLowerCase();
    return profileAccessProfiles.filter((profile) => {
      if (!q) return true;
      return [profile.id, profile.name, profile.category, profile.description]
        .join(" ")
        .toLowerCase()
        .includes(q);
    });
  }, [profileAccessProfiles, profileAccessSearch]);

  const profileAccessIds = filteredProfileAccessProfiles.map((profile) => profile.id);
  const selectedProfileAccessCount = profileAccessIds.filter((id) =>
    draft.allowedProfileIds.includes(id)
  ).length;
  const toggleProfileAccess = (profileId: string) => {
    if (profileAccessReadOnly) return;
    setDraft((current) => ({
      ...current,
      allowedProfileIds: current.allowedProfileIds.includes(profileId)
        ? current.allowedProfileIds.filter((value) => value !== profileId)
        : [...current.allowedProfileIds, profileId],
    }));
  };
  const selectProfileAccess = (ids: string[]) => {
    if (profileAccessReadOnly) return;
    setDraft((current) => ({
      ...current,
      allowedProfileIds: [...new Set([...current.allowedProfileIds, ...ids])],
    }));
  };
  const clearProfileAccess = (ids: string[]) => {
    if (profileAccessReadOnly) return;
    const idSet = new Set(ids);
    setDraft((current) => ({
      ...current,
      allowedProfileIds: current.allowedProfileIds.filter((id) => !idSet.has(id)),
    }));
  };

  const roleColumns: Array<DataTableColumn<SecurityRole>> = [
    {
      key: "role",
      header: t("security.roles.column.role"),
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
            aria-label={t("security.roles.showRole", { name: role.role_code })}
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
      header: t("security.common.status"),
      className: "min-w-32 align-top",
      render: (role) => <RoleStatusBadges role={role} />,
    },
    {
      key: "permissions",
      header: t("security.roles.permissions"),
      sortable: true,
      className: "min-w-28 align-top",
      render: (role) =>
        role.role_code === SYSTEM_ADMIN_ROLE_CODE
          ? t("security.permissions.all")
          : t("security.roles.permissionCount", {
              count: effectivePermissionCodes(role.permissions, permissionByCode).size,
            }),
    },
    {
      key: "profiles",
      header: t("security.roles.profileAccess"),
      className: "min-w-32 align-top",
      render: (role) =>
        roleGrantsAllProfileAccess(role, permissionByCode)
          ? t("security.roles.profileAccessAll")
          : t("security.roles.profileAccessCount", { count: role.allowed_profile_ids.length }),
    },
  ];

  return (
    <>
      <PageHeader
        wide
        title={t("nav.securityPermissions")}
        subtitle={t("security.permissions.subtitle")}
        actions={
          activeView === "list"
            ? [
                {
                  id: "refresh",
                  kind: "utility",
                  label: t("common.action.refresh"),
                  icon: RefreshCw,
                  disabled: operationBusy,
                  onClick: () => load(true),
                  loading,
                },
              ]
            : []
        }
        actionsLabel={t("security.permissions.actionsLabel")}
        actionsTestId="security-permissions-actions"
      />
      <PageBody wide className="grid gap-4">
        {loadError ? <Banner severity="danger">{loadError}</Banner> : null}
        {profileAccessLoadWarning ? (
          <Banner severity="warning">{profileAccessLoadWarning}</Banner>
        ) : null}

        {activeView === "list" ? (
          <SecurityManagementPanelShell
            id="security-permissions-panel-list"
            idPrefix="security-permissions"
            ariaLabel={t("security.permissions.workspaceLabel")}
            splitId="security-permissions-list"
            splitStoragePrefix={FIXED_SPLIT_STORAGE_PREFIX}
            preferredWidePane="right"
          >
            <section
              className="grid min-w-0 content-start gap-3"
              aria-labelledby="security-permissions-list-heading"
            >
              <SecurityPanelHeader
                headingId="security-permissions-list-heading"
                icon={LockKeyhole}
                title={t("security.roles.list")}
                description={t("security.permissions.listHint")}
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
                  label={t("security.common.search")}
                  placeholder={t("security.permissions.searchPlaceholder")}
                  value={search}
                  testId="security-permissions-search"
                  disabled={operationBusy}
                  onChange={setSearch}
                />
              </div>
              {loading ? (
                <ProcessingIndicator
                  active
                  label={t("security.common.loading")}
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
                  className: SECURITY_TABLE_ROW_CLASS,
                  "aria-label": t("security.roles.showRole", { name: role.role_code }),
                })}
                ariaLabel={t("security.roles.list")}
                testId="security-permissions-grid"
                scrollAriaLabel={t("security.common.listScrollLabel", {
                  list: t("security.roles.list"),
                })}
                scrollTestId="security-permissions-scroll-region"
                stickyHeader
                visibleRows={SECURITY_TABLE_VISIBLE_ROWS}
                empty={
                  <EmptyState
                    title={search ? t("security.roles.noResultsTitle") : t("security.common.empty")}
                    hint={search ? t("security.roles.noResultsHint") : undefined}
                  />
                }
                columns={roleColumns}
              />
            </section>

            <PermissionDetailPanel
              role={selectedRole}
              canManage={canManage}
              permissionByCode={permissionByCode}
              profileAccessProfiles={profileAccessProfiles}
              actions={selectedRole ? roleActions(selectedRole) : []}
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
                <span>{t("security.common.backToList")}</span>
              </Button>
            </div>
            <SecurityManagementPanelShell
              id="security-permissions-panel-edit"
              idPrefix="security-permissions"
              ariaLabel={t("security.permissions.taskPanelLabel")}
            >
              <SecurityPanelHeader
                icon={Pencil}
                title={t("security.permissions.formTitle", {
                  role: editingRole?.role_code ?? "",
                })}
                description={t("security.permissions.formHint")}
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
                    <SecurityDetailField label={t("security.roles.code")}>
                      <code className="break-all font-mono text-xs">{editingRole.role_code}</code>
                    </SecurityDetailField>
                    <SecurityDetailField label={t("security.roles.name")}>
                      {editingRole.display_name}
                    </SecurityDetailField>
                  </dl>
                ) : null}

                <fieldset className="grid gap-3" disabled={inputReadOnly}>
                  <legend className="text-base font-semibold">{t("security.roles.permissions")}</legend>
                  <p className="text-sm text-fg-muted">{t("security.roles.permissionsHint")}</p>
                  {permissions.length > 0 ? (
                    <BulkSelectionActions
                      selectLabel={t("common.selection.selectAll")}
                      clearLabel={t("common.selection.clearAll")}
                      selectDisabled={readOnly || selectedPermissionCount === permissionCodes.length}
                      clearDisabled={readOnly || selectedPermissionCount === 0}
                      dataTestId="security-roles-permission-selection-actions"
                      onSelectAll={() => selectPermissions(permissionCodes)}
                      onClearAll={() => clearPermissions(permissionCodes)}
                    />
                  ) : null}
                  {permissionGroups.length === 0 ? (
                    <p className="rounded-md border border-dashed border-border p-4 text-sm text-fg-muted">
                      {t("security.common.empty")}
                    </p>
                  ) : (
                    <div className="grid gap-3 lg:grid-cols-2">
                      {permissionGroups.map(([group, groupPermissions]) => {
                        const groupCodes = groupPermissions.map((permission) => permission.code);
                        const selectedGroupCount = groupCodes.filter((code) =>
                          draft.permissions.includes(code)
                        ).length;
                        return (
                          <div key={group} className="rounded-md border border-border p-3">
                            <div className="mb-2 grid gap-2">
                              <h3 className="text-sm font-semibold">{group}</h3>
                              <BulkSelectionActions
                                selectLabel={t("common.selection.selectAll")}
                                clearLabel={t("common.selection.clearAll")}
                                selectAriaLabel={t("common.selection.selectGroup", { name: group })}
                                clearAriaLabel={t("common.selection.clearGroup", { name: group })}
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
                                const inheritedSources =
                                  draftInheritedPermissionSources.get(permission.code) ?? [];
                                const inherited = !checkedDirect && inheritedSources.length > 0;
                                return (
                                  <label
                                    key={permission.code}
                                    className={`flex min-h-11 items-start gap-2 text-sm ${
                                      readOnly || inherited
                                        ? "cursor-not-allowed opacity-80"
                                        : "cursor-pointer"
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
                                            label={t("security.roles.permissionInherited", {
                                              source: inheritedSources[0],
                                            })}
                                          />
                                        ) : null}
                                      </span>
                                      <span className="block text-xs leading-5 text-fg-muted">
                                        {permission.description}
                                      </span>
                                      <code className="block break-all text-xs text-fg-muted">
                                        {permission.code}
                                      </code>
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

                <fieldset className="grid gap-3" disabled={profileAccessReadOnly}>
                  <legend id="security-roles-profile-access-label" className="text-base font-semibold">
                    {t("security.roles.profileAccess")}
                  </legend>
                  <p className="text-sm text-fg-muted">{t("security.roles.profileAccessHint")}</p>
                  {draftGrantsAllProfileAccess ? (
                    <Banner severity="info">
                      {t(
                        editingRole?.role_code === SYSTEM_ADMIN_ROLE_CODE
                          ? "security.roles.profileAccessSystemAdmin"
                          : "security.roles.profileAccessManagedAll"
                      )}
                    </Banner>
                  ) : (
                    <>
                      <div className="rounded-md border border-border bg-surface-sunken p-3">
                        <SecuritySearchField
                          label={t("security.roles.profileAccessSearch")}
                          placeholder={t("security.roles.profileAccessSearchPlaceholder")}
                          value={profileAccessSearch}
                          testId="security-roles-profile-access-search"
                          disabled={profileAccessReadOnly}
                          onChange={(value) => {
                            if (profileAccessReadOnly) return;
                            setProfileAccessSearch(value);
                          }}
                        />
                      </div>
                      {profileAccessProfiles.length > 0 ? (
                        <BulkSelectionActions
                          selectLabel={t("common.selection.selectAll")}
                          clearLabel={t("common.selection.clearAll")}
                          selectDisabled={
                            profileAccessReadOnly ||
                            profileAccessIds.length === 0 ||
                            selectedProfileAccessCount === profileAccessIds.length
                          }
                          clearDisabled={profileAccessReadOnly || selectedProfileAccessCount === 0}
                          dataTestId="security-roles-profile-access-selection-actions"
                          onSelectAll={() => selectProfileAccess(profileAccessIds)}
                          onClearAll={() => clearProfileAccess(profileAccessIds)}
                        />
                      ) : null}
                      {profileAccessProfiles.length === 0 ? (
                        <p className="rounded-md border border-dashed border-border p-4 text-sm text-fg-muted">
                          {t("security.roles.profileAccessEmpty")}
                        </p>
                      ) : filteredProfileAccessProfiles.length === 0 ? (
                        <p className="rounded-md border border-dashed border-border p-4 text-sm text-fg-muted">
                          {t("security.roles.profileAccessNoResults")}
                        </p>
                      ) : (
                        <div
                          role="region"
                          aria-labelledby="security-roles-profile-access-label"
                          tabIndex={0}
                          data-testid="security-roles-profile-access-list"
                          className={`grid min-w-0 gap-2 overflow-x-hidden rounded-md border border-border bg-surface-sunken p-3 pr-4 lg:grid-cols-2 ${SECURITY_LIST_SCROLL_CLASS} ${SECURITY_LIST_FOCUS_CLASS}`}
                        >
                          {filteredProfileAccessProfiles.map((profile) => {
                            const checked = draft.allowedProfileIds.includes(profile.id);
                            return (
                              <label
                                key={profile.id}
                                className={`flex min-h-11 items-start gap-2 text-sm ${
                                  profileAccessReadOnly ? "cursor-not-allowed opacity-80" : "cursor-pointer"
                                }`}
                              >
                                <input
                                  className="mt-0.5 h-4 w-4 accent-accent-emphasis disabled:cursor-not-allowed"
                                  type="checkbox"
                                  checked={checked}
                                  disabled={profileAccessReadOnly}
                                  onChange={() => toggleProfileAccess(profile.id)}
                                />
                                <span className="min-w-0">
                                  <span className="flex flex-wrap items-center gap-1.5 font-medium">
                                    <span>{profileAccessLabel(profile)}</span>
                                  </span>
                                  {profile.description ? (
                                    <span className="block text-xs leading-5 text-fg-muted">
                                      {profile.description}
                                    </span>
                                  ) : null}
                                  <code className="block break-all text-xs text-fg-muted">{profile.id}</code>
                                </span>
                              </label>
                            );
                          })}
                        </div>
                      )}
                    </>
                  )}
                </fieldset>

                <FormActionBar
                  ariaLabel={t("security.permissions.editActions")}
                  testId="security-permissions-form-actions"
                  primaryActions={
                    !readOnly
                      ? [
                          {
                            id: "save",
                            label: t("security.common.save"),
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
                      label: t("security.common.cancel"),
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

function PermissionDetailPanel({
  role,
  canManage,
  permissionByCode,
  profileAccessProfiles,
  actions,
}: {
  role: SecurityRole | null;
  canManage: boolean;
  permissionByCode: Map<string, PermissionDefinition>;
  profileAccessProfiles: ProfileAccessProfile[];
  actions: EntityAction[];
}) {
  if (!role) {
    return (
      <SecurityEmptySelection
        title={t("security.roles.noSelectionTitle")}
        hint={t("security.permissions.noSelectionHint")}
      />
    );
  }

  const effectiveCodes = effectivePermissionCodes(role.permissions, permissionByCode);
  const grantsAllProfileAccess = roleGrantsAllProfileAccess(role, permissionByCode);
  const allowedProfiles = grantsAllProfileAccess
    ? profileAccessProfiles
    : profileAccessProfiles.filter((profile) => role.allowed_profile_ids.includes(profile.id));

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
            ariaLabel={`${t("security.common.actions")}: ${role.role_code}`}
            testId="security-permissions-detail-actions"
          />
        ) : null}
      </div>

      {role.role_code === SYSTEM_ADMIN_ROLE_CODE ? (
        <Banner severity="info">{t("security.roles.systemAdminNotice")}</Banner>
      ) : role.archived ? (
        <Banner severity="warning">{t("security.roles.archivedPermissionNotice")}</Banner>
      ) : null}

      <dl className="grid gap-3 md:grid-cols-2">
        <SecurityDetailField label={t("security.roles.permissions")}>
          {role.role_code === SYSTEM_ADMIN_ROLE_CODE
            ? t("security.permissions.all")
            : t("security.roles.permissionCount", { count: effectiveCodes.size })}
        </SecurityDetailField>
        <SecurityDetailField label={t("security.roles.profileAccess")}>
          {grantsAllProfileAccess
            ? t("security.roles.profileAccessAll")
            : t("security.roles.profileAccessCount", { count: allowedProfiles.length })}
        </SecurityDetailField>
      </dl>
      {!grantsAllProfileAccess && allowedProfiles.length > 0 ? (
        <div className="grid gap-2 rounded-md border border-border bg-surface p-3">
          <h3 className="text-sm font-semibold text-fg">{t("security.roles.profileAccess")}</h3>
          <div className="flex flex-wrap gap-1.5">
            {allowedProfiles.map((profile) => (
              <StatusBadge
                icon={false}
                key={profile.id}
                variant="neutral"
                label={profileAccessLabel(profile)}
              />
            ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}
