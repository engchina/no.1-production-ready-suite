import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";

import {
  Archive,
  ArchiveRestore,
  ArrowLeft,
  Pencil,
  Plus,
  RefreshCw,
  Shield,
  ShieldCheck,
  Trash2,
} from "lucide-react";

import {
  Banner,
  EmptyState,
  FormStatus,
  toast,
  type DataTableColumn,
  type DataTableSort,
} from "@engchina/production-ready-ui";

import { BulkSelectionActions } from "@/components/BulkSelectionActions";
import { FormActionBar, entityActionToFormAction } from "@/components/FormActionBar";
import { MasterDetailDataTable } from "@/components/MasterDetailDataTable";
import { ObjectActionBar, type EntityAction } from "@/components/ObjectActions";
import { PageHeader } from "@/components/PageHeader";
import { ProcessingIndicator } from "@/components/ProcessingState";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/status-badge";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { FieldError } from "@/components/ui/field-error";
import { FieldLabel, RequiredFieldsNote } from "@/components/ui/required-field";
import { ApiError, isAbortError } from "@/lib/api";
import {
  mapApiFieldErrors,
  unmappedApiErrorMessage,
  withoutFieldError,
} from "@/lib/api-field-errors";
import { t } from "@/lib/i18n";
import {
  INFORMATION_LIST_SCROLL_CLASS,
  INFORMATION_TABLE_FOCUS_CLASS,
  INFORMATION_TABLE_ROW_CLASS,
  INFORMATION_TABLE_SCROLL_CLASS,
} from "@/lib/list-density";
import { useRequestScope } from "@/lib/useRequestScope";
import { cn } from "@/lib/utils";
import { selectedVisibleKey } from "@/lib/visible-selection";
import { useAuth } from "./AuthProvider";
import { MENU_PERMISSIONS } from "./menu-permissions";
import {
  SecurityDetailField,
  SecurityEmptySelection,
  SecurityManagementPanelShell,
  SecurityPanelHeader,
  SecuritySearchField,
  securityFilteredCount,
} from "./SecurityManagementShared";
import { securityApi } from "./api";
import type { PermissionDefinition, ProfileAccessProfile, SecurityRole } from "./types";

type RolePanelView = "list" | "create" | "edit";

interface RoleDraftState {
  roleCode: string;
  displayName: string;
  description: string;
  permissions: string[];
  allowedProfileIds: string[];
}

type RoleFormField = "roleCode" | "displayName";
type RoleFieldErrors = Partial<Record<RoleFormField, string>>;

const ROLE_POINTER_TO_FIELD = {
  "/role_code": "roleCode",
  "/display_name": "displayName",
} as const satisfies Readonly<Record<string, RoleFormField>>;

const SYSTEM_ADMIN_ROLE_CODE = "SYSTEM_ADMIN";
const PROFILE_MANAGE_PERMISSION = "nl2sql.profiles.manage";

const EMPTY_DRAFT: RoleDraftState = {
  roleCode: "",
  displayName: "",
  description: "",
  permissions: [],
  allowedProfileIds: [],
};

const INPUT_CLASS =
  "h-11 w-full rounded-md border border-border bg-card px-3 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-ring/30 disabled:bg-muted/20 disabled:text-muted";

function compareText(left: string, right: string, direction: DataTableSort["direction"]) {
  const result = left.localeCompare(right, "ja");
  return direction === "asc" ? result : -result;
}

function compareNumber(left: number, right: number, direction: DataTableSort["direction"]) {
  const result = left - right;
  return direction === "asc" ? result : -result;
}

function roleStatusText(role: SecurityRole) {
  if (role.archived) return t("security.roles.archivedDisabled");
  if (role.is_built_in) return t("security.roles.builtIn");
  return t("security.roles.custom");
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

export function SecurityRolesPage() {
  const confirm = useConfirm();
  const { hasPermission } = useAuth();
  const canManage = hasPermission(MENU_PERMISSIONS.securityRoles);
  const [roles, setRoles] = useState<SecurityRole[]>([]);
  const [permissions, setPermissions] = useState<PermissionDefinition[]>([]);
  const [profileAccessProfiles, setProfileAccessProfiles] = useState<ProfileAccessProfile[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [activeView, setActiveView] = useState<RolePanelView>("list");
  const [search, setSearch] = useState("");
  const [profileAccessSearch, setProfileAccessSearch] = useState("");
  const [sort, setSort] = useState<DataTableSort>({ key: "role", direction: "asc" });
  const [draft, setDraft] = useState<RoleDraftState>(EMPTY_DRAFT);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [deletingRoleId, setDeletingRoleId] = useState<string | null>(null);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [formError, setFormError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<RoleFieldErrors>({});
  const [profileAccessLoadWarning, setProfileAccessLoadWarning] = useState("");
  const formRef = useRef<HTMLFormElement | null>(null);
  const roleCodeRef = useRef<HTMLInputElement | null>(null);
  const displayNameRef = useRef<HTMLInputElement | null>(null);
  const loadSequence = useRef(0);
  const selectedRoleManualSelection = useRef(false);
  const { abortAll, run: runScopedRequest } = useRequestScope();

  const editingRole = roles.find((role) => role.role_id === editingId) ?? null;
  const readOnly = Boolean(!canManage || editingRole?.is_built_in || editingRole?.archived);
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
  const profileAccessReadOnly = readOnly || draftGrantsAllProfileAccess;

  const rolePermissionText = (role: SecurityRole) =>
    [...effectivePermissionCodes(role.permissions, permissionByCode)]
      .map((code) => permissionByCode.get(code)?.label ?? code)
      .join(" ");

  const roleProfileAccessText = (role: SecurityRole) =>
    roleGrantsAllProfileAccess(role, permissionByCode)
      ? t("security.roles.profileAccessAll")
      : profileAccessProfiles
          .filter((profile) => role.allowed_profile_ids.includes(profile.id))
          .map(profileAccessLabel)
          .join(" ");

  const roleSearchText = (role: SecurityRole) =>
    [
      role.role_code,
      role.display_name,
      role.description,
      roleStatusText(role),
      rolePermissionText(role),
      roleProfileAccessText(role),
    ]
      .join(" ")
      .toLowerCase();

  const filteredRoles = useMemo(() => {
    const q = search.trim().toLowerCase();
    return roles
      .filter((role) => (q ? roleSearchText(role).includes(q) : true))
      .sort((left, right) => {
        if (sort.key === "status") return compareText(roleStatusText(left), roleStatusText(right), sort.direction);
        if (sort.key === "permissions") {
          return compareNumber(
            effectivePermissionCodes(left.permissions, permissionByCode).size,
            effectivePermissionCodes(right.permissions, permissionByCode).size,
            sort.direction
          );
        }
        return compareText(left.display_name, right.display_name, sort.direction);
      });
  }, [permissionByCode, roles, search, sort]);

  const visibleSelectedId =
    activeView === "list"
      ? selectedVisibleKey(filteredRoles, selectedId, (role) => role.role_id, {
          preserveSelected: selectedRoleManualSelection.current,
        })
      : selectedId;
  const selectedRole = roles.find((role) => role.role_id === visibleSelectedId) ?? null;

  const load = async (announce = false) => {
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    setLoading(true);
    setLoadError("");
    setActionError("");
    setProfileAccessLoadWarning("");
    try {
      await runScopedRequest(async (signal) => {
        const profileRowsRequest = canManage
          ? securityApi
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
              })
          : Promise.resolve({ rows: [] as ProfileAccessProfile[], warning: "" });
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
        setSelectedId((current) =>
          current && roleRows.some((role) => role.role_id === current)
            ? current
            : null
        );
      });
      if (announce && sequence === loadSequence.current) {
        toast.success(t("common.action.refreshed"));
      }
    } catch (cause) {
      if (isAbortError(cause)) {
        return;
      }
      const nextError =
        cause instanceof Error && cause.message.trim()
          ? cause.message
          : t("security.common.loadError");
      if (sequence === loadSequence.current) setLoadError(nextError);
    } finally {
      if (sequence === loadSequence.current) setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    return () => {
      loadSequence.current += 1;
      abortAll();
    };
  }, []);

  useEffect(() => {
    if (activeView !== "list" || loading) return;
    setSelectedId((current) => {
      const nextId = selectedVisibleKey(filteredRoles, current, (role) => role.role_id, {
        preserveSelected: selectedRoleManualSelection.current,
      });
      if (nextId !== current) selectedRoleManualSelection.current = false;
      return nextId;
    });
  }, [activeView, filteredRoles, loading]);

  const clearFieldError = (field: RoleFormField) => {
    setFieldErrors((current) => withoutFieldError(current, field));
    setFormError("");
  };

  const focusFirstFieldError = (errors: RoleFieldErrors) => {
    window.requestAnimationFrame(() => {
      if (errors.roleCode) roleCodeRef.current?.focus();
      else if (errors.displayName) displayNameRef.current?.focus();
    });
  };

  const startCreate = () => {
    setActiveView("create");
    setEditingId(null);
    setDraft(EMPTY_DRAFT);
    setProfileAccessSearch("");
    setFormError("");
    setFieldErrors({});
  };

  const startEdit = (role: SecurityRole) => {
    selectedRoleManualSelection.current = true;
    setSelectedId(role.role_id);
    setEditingId(role.role_id);
    setActiveView("edit");
    setDraft({
      roleCode: role.role_code,
      displayName: role.display_name,
      description: role.description,
      permissions: role.permissions,
      allowedProfileIds: role.allowed_profile_ids,
    });
    setProfileAccessSearch("");
    setFormError("");
    setFieldErrors({});
  };

  const returnToList = () => {
    setActiveView("list");
    setEditingId(null);
    setFormError("");
    setFieldErrors({});
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || readOnly) return;
    const normalizedRoleCode = draft.roleCode.trim().toUpperCase();
    if (activeView === "create" && normalizedRoleCode === SYSTEM_ADMIN_ROLE_CODE) {
      const nextErrors = { roleCode: t("security.roles.codeReserved") };
      setFormError("");
      setFieldErrors(nextErrors);
      focusFirstFieldError(nextErrors);
      return;
    }
    setBusy(true);
    setFormError("");
    setFieldErrors({});
    try {
      if (activeView === "edit") {
        if (!editingRole) return;
        const updated = await securityApi.updateRole({
          ...editingRole,
          display_name: draft.displayName,
          description: draft.description,
          permissions: draft.permissions,
          allowed_profile_ids: draftGrantsAllProfileAccess ? [] : draft.allowedProfileIds,
          data_entitlements: editingRole.data_entitlements,
        });
        const nextRole = normalizedRole(updated);
        setRoles((rows) => rows.map((row) => (row.role_id === nextRole.role_id ? nextRole : row)));
        startEdit(nextRole);
      } else {
        const created = await securityApi.createRole({
          role_code: draft.roleCode,
          display_name: draft.displayName,
          description: draft.description,
          permissions: draft.permissions,
          data_entitlements: [],
          allowed_profile_ids: draftGrantsAllProfileAccess ? [] : draft.allowedProfileIds,
        });
        const nextRole = normalizedRole(created);
        setRoles((rows) => [...rows, nextRole]);
        startEdit(nextRole);
      }
      toast.success(t("security.common.saved"));
    } catch (cause) {
      const nextErrors = mapApiFieldErrors(
        cause,
        ROLE_POINTER_TO_FIELD,
        (problem, apiError) =>
          apiError.errorCode === "SECURITY_ROLE_CODE_CONFLICT" &&
          problem.pointer === "/role_code"
            ? t("security.roles.codeConflict")
            : apiError.errorCode === "SECURITY_ROLE_CODE_RESERVED" &&
                problem.pointer === "/role_code"
              ? t("security.roles.codeReserved")
            : problem.message
      );
      setFieldErrors(nextErrors);
      setFormError(
        unmappedApiErrorMessage(cause, ROLE_POINTER_TO_FIELD, t("security.common.saveError"))
      );
      if (cause instanceof ApiError && Object.keys(nextErrors).length > 0) {
        focusFirstFieldError(nextErrors);
      }
    } finally {
      setBusy(false);
    }
  };

  const handleArchive = async (role: SecurityRole) => {
    if (
      !(await confirm({
        title: t("security.roles.archive"),
        description: t("security.roles.archiveConfirm"),
        tone: "danger",
      }))
    ) {
      return;
    }
    try {
      const archived = await securityApi.archiveRole(role);
      setRoles((rows) => rows.map((row) => (row.role_id === archived.role_id ? archived : row)));
      selectedRoleManualSelection.current = true;
      setSelectedId(archived.role_id);
      returnToList();
      toast.success(t("security.common.saved"));
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : t("security.common.saveError"));
    }
  };

  const handleRestore = async (role: SecurityRole) => {
    if (
      !(await confirm({
        title: t("security.roles.restore"),
        description: t("security.roles.restoreConfirm"),
        tone: "warning",
      }))
    ) {
      return;
    }
    try {
      const restored = await securityApi.restoreRole(role);
      setRoles((rows) => rows.map((row) => (row.role_id === restored.role_id ? restored : row)));
      selectedRoleManualSelection.current = true;
      setSelectedId(restored.role_id);
      returnToList();
      toast.success(t("security.common.saved"));
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : t("security.common.saveError"));
    }
  };

  const canDeleteRole = (role: SecurityRole) => !role.is_built_in && role.archived;

  const handleDelete = async (role: SecurityRole) => {
    if (busy || deletingRoleId !== null || !canDeleteRole(role)) return;
    selectedRoleManualSelection.current = true;
    setSelectedId(role.role_id);
    if (
      !(await confirm({
        title: t("security.roles.delete"),
        description: t("security.roles.deleteConfirm", {
          name: role.display_name,
          code: role.role_code,
        }),
        confirmLabel: t("common.delete"),
        tone: "danger",
        dismissOnOverlay: false,
      }))
    ) {
      return;
    }

    setActionError("");
    setDeletingRoleId(role.role_id);
    try {
      await securityApi.deleteRole(role);
      const deletedIndex = filteredRoles.findIndex((row) => row.role_id === role.role_id);
      const nextRole =
        filteredRoles[deletedIndex + 1] ?? filteredRoles[deletedIndex - 1] ?? null;
      setRoles((rows) => rows.filter((row) => row.role_id !== role.role_id));
      selectedRoleManualSelection.current = Boolean(nextRole);
      setSelectedId(nextRole?.role_id ?? null);
      setEditingId(null);
      setActiveView("list");
      setFormError("");
      setFieldErrors({});
      toast.success(t("security.roles.deleteSuccess", { name: role.display_name }));
    } catch (cause) {
      setActionError(
        cause instanceof Error && cause.message.trim()
          ? cause.message
          : t("security.roles.deleteError")
      );
    } finally {
      setDeletingRoleId(null);
    }
  };

  const roleActions = (role: SecurityRole): EntityAction[] =>
    canManage
      ? [
          {
            id: "edit",
            label: t("security.common.edit"),
            icon: Pencil,
            onSelect: () => startEdit(role),
          },
          {
            id: "archive",
            label: t("security.roles.archive"),
            icon: Archive,
            tone: "danger",
            visible: !role.is_built_in && !role.archived,
            disabled: busy || deletingRoleId !== null,
            onSelect: () => handleArchive(role),
          },
          {
            id: "restore",
            label: t("security.roles.restore"),
            icon: ArchiveRestore,
            visible: !role.is_built_in && role.archived,
            disabled: busy || deletingRoleId !== null,
            onSelect: () => handleRestore(role),
          },
          {
            id: "delete",
            label: t("security.roles.delete"),
            icon: Trash2,
            tone: "danger",
            visible: canDeleteRole(role),
            loading: deletingRoleId === role.role_id,
            disabled: busy || deletingRoleId !== null,
            onSelect: () => handleDelete(role),
          },
        ]
      : [];

  const formRoleActions = (...actionIds: string[]) => {
    if (!editingRole) return [];
    const actions = roleActions(editingRole);
    return actionIds.flatMap((actionId) => {
      const action = actions.find(
        (candidate) => candidate.id === actionId && candidate.visible !== false
      );
      return action ? [entityActionToFormAction(action)] : [];
    });
  };

  const togglePermission = (code: string) => {
    if (readOnly) return;
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
    if (readOnly) return;
    setDraft((current) => ({
      ...current,
      permissions: [...new Set([...current.permissions, ...codes])],
    }));
  };
  const clearPermissions = (codes: string[]) => {
    if (readOnly) return;
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
      className: "min-w-52",
      render: (role) => {
        const selected = visibleSelectedId === role.role_id;
        return (
          <button
            type="button"
            className={`min-w-0 cursor-pointer text-left focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring ${
              selected ? "text-primary" : "text-foreground"
            }`}
            aria-label={t("security.roles.showRole", { name: role.display_name })}
            aria-current={selected ? "true" : undefined}
            onClick={(event) => {
              event.stopPropagation();
              selectedRoleManualSelection.current = true;
              setSelectedId(role.role_id);
            }}
          >
            <span className="block break-words font-medium">{role.display_name}</span>
            <span className="block break-all font-mono text-[11px] text-muted">{role.role_code}</span>
          </button>
        );
      },
    },
    {
      key: "status",
      header: t("security.common.status"),
      sortable: true,
      className: "min-w-32",
      render: (role) => <RoleStatusBadges role={role} />,
    },
    {
      key: "permissions",
      header: t("security.roles.permissions"),
      sortable: true,
      className: "min-w-32",
      render: (role) =>
        t("security.roles.permissionCount", {
          count: effectivePermissionCodes(role.permissions, permissionByCode).size,
        }),
    },
  ];

  return (
    <>
      <PageHeader
        title={t("nav.securityRoles")}
        subtitle={t("security.roles.subtitle")}
        actions={
          activeView === "list"
            ? [
                ...(canManage
                  ? [
                      {
                        id: "create-role",
                        kind: "primary" as const,
                        label: t("security.common.create"),
                        icon: Plus,
                        onClick: startCreate,
                      },
                    ]
                  : []),
                {
                  id: "refresh",
                  kind: "utility",
                  label: t("common.action.refresh"),
                  icon: RefreshCw,
                  onClick: () => load(true),
                  loading,
                },
              ]
            : []
        }
        actionsAriaLabel={t("security.roles.actionsLabel")}
        actionsTestId="security-roles-actions"
      />
      <main className="grid gap-4 p-4 lg:p-8">
        {loadError ? <Banner severity="danger">{loadError}</Banner> : null}
        {profileAccessLoadWarning ? (
          <Banner severity="warning">{profileAccessLoadWarning}</Banner>
        ) : null}
        {actionError ? <Banner severity="danger">{actionError}</Banner> : null}

        {activeView === "list" ? (
          <SecurityManagementPanelShell
              id="security-roles-panel-list"
              idPrefix="security-roles"
              ariaLabel={t("security.roles.workspaceLabel")}
              splitId="security-roles-list"
              preferredWidePane="right"
            >
              <section className="grid min-w-0 content-start gap-3" aria-labelledby="security-roles-list-heading">
                <SecurityPanelHeader
                  headingId="security-roles-list-heading"
                  icon={Shield}
                  title={t("security.roles.list")}
                  description={t("security.roles.listHint")}
                  action={<StatusBadge variant="info" label={securityFilteredCount(filteredRoles.length, roles.length)} />}
                />
                <div className="rounded-md border border-border bg-background p-3">
                  <SecuritySearchField
                    label={t("security.common.search")}
                    placeholder={t("security.roles.searchPlaceholder")}
                    value={search}
                    testId="security-roles-search"
                    onChange={setSearch}
                  />
                </div>
                {loading ? (
                  <ProcessingIndicator
                    active
                    label={t("security.common.loading")}
                    operationKey="security-roles-load"
                    placement="panel"
                    testId="security-roles-loading"
                    activityIcon="none"
                  />
                ) : null}
                <MasterDetailDataTable
                  dense
                  loading={loading}
                  rows={filteredRoles}
                  sort={sort}
                  onSortChange={setSort}
                  selectedRowKey={visibleSelectedId}
                  onRowSelect={(role) => {
                    selectedRoleManualSelection.current = true;
                    setSelectedId(role.role_id);
                  }}
                  getRowKey={(role) => role.role_id}
                  getRowAriaLabel={(role) => t("security.roles.showRole", { name: role.display_name })}
                  ariaLabel={t("security.roles.list")}
                  testId="security-roles-grid"
                  scrollAriaLabel={t("security.common.listScrollLabel", {
                    list: t("security.roles.list"),
                  })}
                  scrollTestId="security-roles-scroll-region"
                  scrollClassName={`${INFORMATION_TABLE_SCROLL_CLASS} ${INFORMATION_TABLE_FOCUS_CLASS}`}
                  className="[&_thead]:sticky [&_thead]:top-0 [&_thead]:z-10 [&_thead_tr]:h-10"
                  rowClassName={INFORMATION_TABLE_ROW_CLASS}
                  empty={<EmptyState title={search ? t("security.roles.noResultsTitle") : t("security.common.empty")} hint={search ? t("security.roles.noResultsHint") : undefined} />}
                  columns={roleColumns}
                />
              </section>

              <RoleDetailPanel
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
              <Button type="button" variant="ghost" size="sm" onClick={returnToList}>
                <ArrowLeft size={15} aria-hidden="true" />
                <span>{t("security.common.backToList")}</span>
              </Button>
            </div>
            <SecurityManagementPanelShell
              id={`security-roles-panel-${activeView}`}
              idPrefix="security-roles"
              ariaLabel={t("security.roles.taskPanelLabel")}
            >
              <SecurityPanelHeader
                icon={activeView === "create" ? Plus : Pencil}
                title={activeView === "edit" ? t("security.roles.form.edit") : t("security.roles.form.create")}
                description={t("security.roles.formHint")}
                headingId="security-roles-form-heading"
              />
              <form
                ref={formRef}
                className="grid gap-6"
                onSubmit={handleSubmit}
                aria-labelledby="security-roles-form-heading"
              >
                <RequiredFieldsNote />
                {editingRole?.role_code === SYSTEM_ADMIN_ROLE_CODE ? (
                  <Banner severity="info">{t("security.roles.systemAdminNotice")}</Banner>
                ) : null}
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="grid gap-1.5 text-sm font-medium">
                    <FieldLabel htmlFor="security-role-code" label={t("security.roles.code")} required />
                    <input
                      ref={roleCodeRef}
                      id="security-role-code"
                      required
                      disabled={activeView === "edit"}
                      className={cn(INPUT_CLASS, fieldErrors.roleCode && "border-danger")}
                      aria-invalid={fieldErrors.roleCode ? "true" : undefined}
                      aria-describedby={fieldErrors.roleCode ? "security-role-code-error" : undefined}
                      value={draft.roleCode}
                      onChange={(event) => {
                        if (readOnly) return;
                        setDraft((current) => ({
                          ...current,
                          roleCode: event.target.value.toUpperCase(),
                        }));
                        clearFieldError("roleCode");
                      }}
                    />
                    <FieldError id="security-role-code-error" message={fieldErrors.roleCode} />
                  </div>
                  <div className="grid gap-1.5 text-sm font-medium">
                    <FieldLabel htmlFor="security-role-name" label={t("security.roles.name")} required />
                    <input
                      ref={displayNameRef}
                      id="security-role-name"
                      required
                      disabled={readOnly}
                      className={cn(INPUT_CLASS, fieldErrors.displayName && "border-danger")}
                      aria-invalid={fieldErrors.displayName ? "true" : undefined}
                      aria-describedby={fieldErrors.displayName ? "security-role-name-error" : undefined}
                      value={draft.displayName}
                      onChange={(event) => {
                        if (readOnly) return;
                        setDraft((current) => ({ ...current, displayName: event.target.value }));
                        clearFieldError("displayName");
                      }}
                    />
                    <FieldError id="security-role-name-error" message={fieldErrors.displayName} />
                  </div>
                </div>
                <label className="grid gap-1.5 text-sm font-medium">
                  <span>{t("security.roles.description")}</span>
                  <textarea
                    disabled={readOnly}
                    className="min-h-24 w-full rounded-md border border-border bg-card px-3 py-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-ring/30 disabled:cursor-not-allowed disabled:bg-muted/20 disabled:text-muted"
                    value={draft.description}
                    onChange={(event) => {
                      if (readOnly) return;
                      setDraft((current) => ({ ...current, description: event.target.value }));
                    }}
                  />
                </label>

                <fieldset className="grid gap-3" disabled={readOnly}>
                  <legend className="text-base font-semibold">{t("security.roles.permissions")}</legend>
                  <p className="text-sm text-muted">{t("security.roles.permissionsHint")}</p>
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
                    <p className="rounded-md border border-dashed border-border p-4 text-sm text-muted">{t("security.common.empty")}</p>
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
                                      className="mt-0.5 h-4 w-4 accent-primary disabled:cursor-not-allowed"
                                      type="checkbox"
                                      checked={checkedDirect || inherited}
                                      disabled={readOnly || inherited}
                                      onChange={() => {
                                        if (!inherited) togglePermission(permission.code);
                                      }}
                                    />
                                    <span className="min-w-0">
                                      <span className="flex flex-wrap items-center gap-1.5 font-medium">
                                        <span>{permission.label}</span>
                                        {inherited ? (
                                          <StatusBadge
                                            variant="neutral"
                                            label={t("security.roles.permissionInherited", {
                                              source: inheritedSources[0],
                                            })}
                                          />
                                        ) : null}
                                      </span>
                                      <span className="block text-xs leading-5 text-muted">{permission.description}</span>
                                      <code className="block break-all text-[10px] text-muted">{permission.code}</code>
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
                  <legend
                    id="security-roles-profile-access-label"
                    className="text-base font-semibold"
                  >
                    {t("security.roles.profileAccess")}
                  </legend>
                  <p className="text-sm text-muted">{t("security.roles.profileAccessHint")}</p>
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
                      <div className="rounded-md border border-border bg-background p-3">
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
                          selectDisabled={profileAccessReadOnly || profileAccessIds.length === 0 || selectedProfileAccessCount === profileAccessIds.length}
                          clearDisabled={profileAccessReadOnly || selectedProfileAccessCount === 0}
                          dataTestId="security-roles-profile-access-selection-actions"
                          onSelectAll={() => selectProfileAccess(profileAccessIds)}
                          onClearAll={() => clearProfileAccess(profileAccessIds)}
                        />
                      ) : null}
                      {profileAccessProfiles.length === 0 ? (
                        <p className="rounded-md border border-dashed border-border p-4 text-sm text-muted">
                          {t("security.roles.profileAccessEmpty")}
                        </p>
                      ) : filteredProfileAccessProfiles.length === 0 ? (
                        <p className="rounded-md border border-dashed border-border p-4 text-sm text-muted">
                          {t("security.roles.profileAccessNoResults")}
                        </p>
                      ) : (
                        <div
                          role="region"
                          aria-labelledby="security-roles-profile-access-label"
                          tabIndex={0}
                          data-testid="security-roles-profile-access-list"
                          className={`grid min-w-0 gap-2 overflow-x-hidden rounded-md border border-border bg-background p-3 pr-4 lg:grid-cols-2 ${INFORMATION_LIST_SCROLL_CLASS} ${INFORMATION_TABLE_FOCUS_CLASS}`}
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
                                  className="mt-0.5 h-4 w-4 accent-primary disabled:cursor-not-allowed"
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
                                    <span className="block text-xs leading-5 text-muted">{profile.description}</span>
                                  ) : null}
                                  <code className="block break-all text-[10px] text-muted">{profile.id}</code>
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
                  ariaLabel={t("security.roles.editActions")}
                  primaryActions={
                    !readOnly
                      ? [
                          {
                            id: "save",
                            label: activeView === "edit" ? t("security.common.save") : t("security.common.create"),
                            loading: busy,
                            onClick: () => {
                              formRef.current?.requestSubmit();
                            },
                          },
                        ]
                      : []
                  }
                  secondaryActions={[
                    ...formRoleActions("restore"),
                    {
                      id: "cancel",
                      label: t("security.common.cancel"),
                      disabled: busy || deletingRoleId !== null,
                      onClick: returnToList,
                    },
                  ]}
                  dangerActions={
                    editingRole ? formRoleActions("archive", "delete") : []
                  }
                  status={
                    <FormStatus
                      tone="danger"
                      message={formError || (activeView === "edit" ? actionError : "")}
                    />
                  }
                />
              </form>
            </SecurityManagementPanelShell>
          </>
        )}
      </main>
    </>
  );
}

function RoleStatusBadges({ role }: { role: SecurityRole }) {
  return (
    <div className="flex flex-wrap gap-1">
      <StatusBadge variant={role.is_built_in ? "info" : "neutral"} label={role.is_built_in ? t("security.roles.builtIn") : t("security.roles.custom")} />
      {role.archived ? <StatusBadge variant="neutral" label={t("security.roles.archivedDisabled")} /> : null}
    </div>
  );
}

function RoleDetailPanel({
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
        hint={t("security.roles.noSelectionHint")}
      />
    );
  }

  const inheritedSources = permissionInheritanceSources(role.permissions, permissionByCode);
  const effectivePermissionCount = role.permissions.length + [...inheritedSources.keys()].filter(
    (code) => !role.permissions.includes(code)
  ).length;
  const grantsAllProfileAccess = roleGrantsAllProfileAccess(role, permissionByCode);
  const allowedProfiles =
    grantsAllProfileAccess
      ? profileAccessProfiles
      : profileAccessProfiles.filter((profile) => role.allowed_profile_ids.includes(profile.id));

  return (
    <section className="grid min-w-0 content-start gap-4 rounded-md border border-border bg-background p-4" aria-labelledby="security-roles-detail-heading">
      <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 id="security-roles-detail-heading" className="flex min-w-0 items-center gap-2 text-base font-semibold text-foreground">
              <ShieldCheck size={18} aria-hidden="true" />
              <span className="min-w-0 break-words">{role.display_name}</span>
            </h2>
            <RoleStatusBadges role={role} />
          </div>
          <p className="mt-1 break-all font-mono text-xs text-muted">{role.role_code}</p>
        </div>
        {canManage ? (
          <ObjectActionBar
            actions={actions}
            ariaLabel={`${t("security.common.actions")}: ${role.display_name}`}
            testId="security-roles-detail-actions"
          />
        ) : null}
      </div>

      {role.role_code === SYSTEM_ADMIN_ROLE_CODE ? (
        <Banner severity="info">{t("security.roles.systemAdminNotice")}</Banner>
      ) : null}
      {role.archived ? (
        <Banner severity="warning">{t("security.roles.archivedPermissionNotice")}</Banner>
      ) : null}

      <dl className="grid gap-3 md:grid-cols-2">
        <SecurityDetailField label={t("security.roles.code")}>
          <code className="break-all font-mono text-xs">{role.role_code}</code>
        </SecurityDetailField>
        <SecurityDetailField label={t("security.common.status")}>
          <RoleStatusBadges role={role} />
        </SecurityDetailField>
        <SecurityDetailField label={t("security.roles.permissions")}>
          {t("security.roles.permissionCount", { count: effectivePermissionCount })}
        </SecurityDetailField>
        <SecurityDetailField label={t("security.roles.profileAccess")}>
          {grantsAllProfileAccess
            ? t("security.roles.profileAccessAll")
            : t("security.roles.profileAccessCount", { count: allowedProfiles.length })}
        </SecurityDetailField>
        <SecurityDetailField label={t("security.common.version")}>
          {String(role.version)}
        </SecurityDetailField>
        <SecurityDetailField label={t("security.roles.description")}>
          {role.description || t("security.common.none")}
        </SecurityDetailField>
      </dl>
      {!grantsAllProfileAccess && allowedProfiles.length > 0 ? (
        <div className="grid gap-2 rounded-md border border-border bg-card p-3">
          <h3 className="text-sm font-semibold text-foreground">{t("security.roles.profileAccess")}</h3>
          <div className="flex flex-wrap gap-1.5">
            {allowedProfiles.map((profile) => (
              <StatusBadge key={profile.id} variant="neutral" label={profileAccessLabel(profile)} />
            ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}
