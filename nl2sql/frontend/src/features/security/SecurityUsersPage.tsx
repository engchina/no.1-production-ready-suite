import { Button } from "@/components/ui/button";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  ArrowLeft,
  Copy,
  KeyRound,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
  UserCheck,
  UserRound,
  Users,
  UserX,
} from "lucide-react";

import {
  Banner,
  EmptyState,
  FormStatus,
  toast,
  type DataTableColumn,
  type DataTableSort,
} from "@engchina/production-ready-ui";

import { FormActionBar, entityActionToFormAction } from "@/components/FormActionBar";
import { MasterDetailDataTable } from "@/components/MasterDetailDataTable";
import { PageHeader } from "@/components/PageHeader";
import { ObjectActionBar, type EntityAction } from "@/components/ObjectActions";
import { ProcessingIndicator } from "@/components/ProcessingState";
import { ErrorState } from "@/components/StateViews";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { StatusBadge } from "@/components/ui/status-badge";
import { FieldError } from "@/components/ui/field-error";
import { FieldLabel, FieldLegend, RequiredFieldsNote } from "@/components/ui/required-field";
import { ApiError, isAbortError } from "@/lib/api";
import {
  mapApiFieldErrors,
  unmappedApiErrorMessage,
  withoutFieldError,
} from "@/lib/api-field-errors";
import { copyTextToClipboard } from "@/lib/clipboard";
import { t } from "@/lib/i18n";
import {
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
import type { AssignedRole, SecurityRole, SecurityUser } from "./types";

type UserPanelView = "list" | "create" | "edit";

interface ResetPasswordError {
  userUuid: string;
  message: string;
}

interface UserDraftState {
  loginUserId: string;
  displayName: string;
  selectedRoleId: string;
  temporaryPassword: string;
}

type UserFormField = keyof UserDraftState;
type UserFieldErrors = Partial<Record<UserFormField, string>>;

const USER_POINTER_TO_FIELD = {
  "/login_user_id": "loginUserId",
  "/display_name": "displayName",
  "/role_ids": "selectedRoleId",
  "/temporary_password": "temporaryPassword",
} as const satisfies Readonly<Record<string, UserFormField>>;

const EMPTY_DRAFT: UserDraftState = {
  loginUserId: "",
  displayName: "",
  selectedRoleId: "",
  temporaryPassword: "",
};

const INPUT_CLASS =
  "h-11 w-full rounded-md border border-border bg-card px-3 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-ring/30 read-only:cursor-default read-only:bg-muted/20 read-only:text-muted disabled:bg-muted/20 disabled:text-muted";
const SYSTEM_ADMIN_ROLE_CODE = "SYSTEM_ADMIN";

function compareText(left: string, right: string, direction: DataTableSort["direction"]) {
  const result = left.localeCompare(right, "ja");
  return direction === "asc" ? result : -result;
}

function userStatusLabel(user: SecurityUser) {
  return user.status === "ACTIVE" ? t("security.common.active") : t("security.common.disabled");
}

export function SecurityUsersPage() {
  const confirm = useConfirm();
  const { hasPermission, user: currentUser } = useAuth();
  const canManage = hasPermission(MENU_PERMISSIONS.securityUsers);
  const [users, setUsers] = useState<SecurityUser[]>([]);
  const [roles, setRoles] = useState<SecurityRole[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [formError, setFormError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<UserFieldErrors>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [activeView, setActiveView] = useState<UserPanelView>("list");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<DataTableSort>({ key: "user", direction: "asc" });
  const [draft, setDraft] = useState<UserDraftState>(EMPTY_DRAFT);
  const [copyPasswordError, setCopyPasswordError] = useState("");
  const [resettingUserId, setResettingUserId] = useState<string | null>(null);
  const [statusChangingUserId, setStatusChangingUserId] = useState<string | null>(null);
  const [deletingUserId, setDeletingUserId] = useState<string | null>(null);
  const [resetPasswordError, setResetPasswordError] = useState<ResetPasswordError | null>(null);
  const formRef = useRef<HTMLFormElement | null>(null);
  const loadSequence = useRef(0);
  const loginUserIdRef = useRef<HTMLInputElement | null>(null);
  const displayNameRef = useRef<HTMLInputElement | null>(null);
  const temporaryPasswordRef = useRef<HTMLInputElement | null>(null);
  const roleGroupRef = useRef<HTMLDivElement | null>(null);
  const selectedUserManualSelection = useRef(false);
  const { abortAll, run: runScopedRequest } = useRequestScope();

  const roleById = useMemo(
    () => new Map(roles.map((role) => [role.role_id, role])),
    [roles]
  );
  const systemAdminRoleId = useMemo(
    () => roles.find((role) => role.role_code === SYSTEM_ADMIN_ROLE_CODE)?.role_id ?? null,
    [roles]
  );

  const editingUser = users.find((user) => user.user_uuid === editingId) ?? null;
  const userFormReadOnly = activeView === "edit" && editingUser?.status !== "ACTIVE";
  const canSubmitUserForm = !userFormReadOnly;
  const accountActionBusy =
    resettingUserId !== null || statusChangingUserId !== null || deletingUserId !== null;
  const assignedRoles = (user: SecurityUser): AssignedRole[] => {
    if (user.assigned_roles?.length) return user.assigned_roles;
    return user.role_ids.map((id) => {
      const role = roleById.get(id);
      return role
        ? {
            role_id: role.role_id,
            role_code: role.role_code,
            display_name: role.display_name,
            is_built_in: role.is_built_in,
            archived: role.archived,
          }
        : {
            role_id: id,
            role_code: id,
            display_name: id,
            is_built_in: false,
            archived: true,
          };
    });
  };
  const assignedRoleLabel = (role: AssignedRole) =>
    role.archived
      ? t("security.users.archivedRoleLabel", { role: role.display_name })
      : role.display_name;

  const roleSummary = (user: SecurityUser) => {
    const names = assignedRoles(user).map(assignedRoleLabel);
    return names.length > 0 ? names.join(", ") : t("security.common.none");
  };
  const canAssignSystemAdmin = activeView === "edit" && Boolean(editingUser?.is_bootstrap_admin);
  const isSystemAdminRole = (role: SecurityRole) =>
    role.role_code === SYSTEM_ADMIN_ROLE_CODE || role.role_id === systemAdminRoleId;
  const isSystemAdminRoleDisabled = (role: SecurityRole) =>
    isSystemAdminRole(role) && !canAssignSystemAdmin && draft.selectedRoleId !== role.role_id;
  const systemAdminRoleHint = (role: SecurityRole) => {
    if (!isSystemAdminRole(role) || canAssignSystemAdmin) return "";
    return draft.selectedRoleId === role.role_id
      ? t("security.users.systemAdminLegacyNotice")
      : t("security.users.systemAdminBootstrapOnly");
  };
  const selectKnownRoleId = (roleIds: string[]) => {
    const availableRoleIds = new Set(roles.map((role) => role.role_id));
    return roleIds.find((roleId) => availableRoleIds.has(roleId)) ?? "";
  };

  const filteredUsers = useMemo(() => {
    const q = search.trim().toLowerCase();
    return users
      .filter((user) => {
        if (!q) return true;
        return (
          user.display_name.toLowerCase().includes(q) ||
          user.login_user_id.toLowerCase().includes(q) ||
          user.status.toLowerCase().includes(q) ||
          roleSummary(user).toLowerCase().includes(q)
        );
      })
      .sort((left, right) => {
        if (sort.key === "login") return compareText(left.login_user_id, right.login_user_id, sort.direction);
        if (sort.key === "roles") return compareText(roleSummary(left), roleSummary(right), sort.direction);
        if (sort.key === "status") return compareText(userStatusLabel(left), userStatusLabel(right), sort.direction);
        return compareText(left.display_name, right.display_name, sort.direction);
      });
  }, [roleById, search, sort, users]);

  const visibleSelectedId =
    activeView === "list"
      ? selectedVisibleKey(filteredUsers, selectedId, (user) => user.user_uuid, {
          preserveSelected: selectedUserManualSelection.current,
        })
      : selectedId;
  const selectedUser = users.find((user) => user.user_uuid === visibleSelectedId) ?? null;

  const load = async (announce = false) => {
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    setLoading(true);
    setLoadError("");
    setActionError("");
    try {
      await runScopedRequest(async (signal) => {
        const [userRows, roleRows] = await Promise.all([
          securityApi.users({ signal }),
          securityApi.roles(false, { signal }),
        ]);
        if (signal.aborted || sequence !== loadSequence.current) return;
        setUsers(userRows);
        setRoles(roleRows.filter((role) => !role.archived));
        setSelectedId((current) =>
          current && userRows.some((user) => user.user_uuid === current)
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
      const nextId = selectedVisibleKey(filteredUsers, current, (user) => user.user_uuid, {
        preserveSelected: selectedUserManualSelection.current,
      });
      if (nextId !== current) selectedUserManualSelection.current = false;
      return nextId;
    });
  }, [activeView, filteredUsers, loading]);

  const clearFieldError = (field: UserFormField) => {
    setFieldErrors((current) => withoutFieldError(current, field));
    setFormError("");
  };

  const updateDraftField = <Field extends UserFormField>(
    field: Field,
    value: UserDraftState[Field]
  ) => {
    if (userFormReadOnly) return;
    setDraft((current) => ({ ...current, [field]: value }));
    clearFieldError(field);
  };

  const focusFirstFieldError = (errors: UserFieldErrors) => {
    window.requestAnimationFrame(() => {
      if (errors.loginUserId) loginUserIdRef.current?.focus();
      else if (errors.displayName) displayNameRef.current?.focus();
      else if (errors.temporaryPassword) temporaryPasswordRef.current?.focus();
      else if (errors.selectedRoleId) {
        roleGroupRef.current
          ?.querySelector<HTMLInputElement>('input[type="radio"]:not(:disabled)')
          ?.focus();
      }
    });
  };

  const copyTemporaryPassword = async () => {
    if (activeView !== "edit" || userFormReadOnly || !draft.temporaryPassword) return;
    setCopyPasswordError("");
    try {
      await copyTextToClipboard(draft.temporaryPassword);
      toast.success(t("common.action.copied"));
    } catch {
      setCopyPasswordError(t("security.users.oneTimePassword.copyError"));
    }
  };

  const startCreate = () => {
    setActiveView("create");
    setEditingId(null);
    setDraft(EMPTY_DRAFT);
    setFormError("");
    setFieldErrors({});
    setActionError("");
    setCopyPasswordError("");
    setResetPasswordError(null);
  };

  const startEdit = (user: SecurityUser, temporaryPassword = "") => {
    selectedUserManualSelection.current = true;
    setSelectedId(user.user_uuid);
    setEditingId(user.user_uuid);
    setActiveView("edit");
    setDraft({
      loginUserId: user.login_user_id,
      displayName: user.display_name,
      selectedRoleId: selectKnownRoleId(user.role_ids),
      temporaryPassword,
    });
    setFormError("");
    setFieldErrors({});
    setActionError("");
    setCopyPasswordError("");
    setResetPasswordError(null);
  };

  const returnToList = () => {
    setActiveView("list");
    setEditingId(null);
    setFormError("");
    setFieldErrors({});
    setActionError("");
    setCopyPasswordError("");
    setResetPasswordError(null);
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || accountActionBusy || !canSubmitUserForm) return;
    setFormError("");
    setFieldErrors({});
    setActionError("");
    if (!draft.selectedRoleId) {
      const nextErrors = { selectedRoleId: t("security.users.roleRequired") };
      setFieldErrors(nextErrors);
      focusFirstFieldError(nextErrors);
      return;
    }
    const selectedRoleIds = [draft.selectedRoleId];
    setBusy(true);
    try {
      if (activeView === "edit") {
        if (!editingUser) return;
        const updated = await securityApi.updateUser({
          ...editingUser,
          display_name: draft.displayName,
          role_ids: selectedRoleIds,
        });
        setUsers((rows) => rows.map((row) => (row.user_uuid === updated.user_uuid ? updated : row)));
        selectedUserManualSelection.current = true;
        setSelectedId(updated.user_uuid);
        setDraft((current) => ({
          ...current,
          displayName: updated.display_name,
          selectedRoleId: selectKnownRoleId(updated.role_ids),
        }));
        toast.success(t("security.common.saved"));
      } else {
        const created = await securityApi.createUser({
          login_user_id: draft.loginUserId,
          display_name: draft.displayName,
          role_ids: selectedRoleIds,
          temporary_password: draft.temporaryPassword || undefined,
        });
        setUsers((rows) => [...rows, created.user]);
        startEdit(created.user, created.temporary_password);
        toast.success(t("security.common.saved"));
      }
    } catch (cause) {
      const nextErrors = mapApiFieldErrors(
        cause,
        USER_POINTER_TO_FIELD,
        (problem, apiError) =>
          apiError.errorCode === "SECURITY_USER_LOGIN_ID_CONFLICT" &&
          problem.pointer === "/login_user_id"
            ? t("security.users.loginUserIdConflict")
            : problem.message
      );
      setFieldErrors(nextErrors);
      setFormError(
        unmappedApiErrorMessage(cause, USER_POINTER_TO_FIELD, t("security.common.saveError"))
      );
      if (cause instanceof ApiError && Object.keys(nextErrors).length > 0) {
        focusFirstFieldError(nextErrors);
      }
    } finally {
      setBusy(false);
    }
  };

  const handleToggleStatus = async (user: SecurityUser) => {
    if (accountActionBusy || busy) return;
    const enabling = user.status !== "ACTIVE";
    if (
      !enabling &&
      !(await confirm({
        title: t("security.users.disable"),
        description: t("security.users.disableConfirm"),
        tone: "danger",
      }))
    ) {
      return;
    }
    setActionError("");
    setResetPasswordError(null);
    setStatusChangingUserId(user.user_uuid);
    try {
      const updated = await securityApi.setUserEnabled(user, enabling);
      setUsers((rows) => rows.map((row) => (row.user_uuid === updated.user_uuid ? updated : row)));
      selectedUserManualSelection.current = true;
      setSelectedId(updated.user_uuid);
      toast.success(
        t(enabling ? "security.users.enableSuccess" : "security.users.disableSuccess")
      );
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : t("security.common.saveError"));
    } finally {
      setStatusChangingUserId(null);
    }
  };

  const handleResetPassword = async (user: SecurityUser) => {
    if (user.status !== "ACTIVE" || accountActionBusy || busy) return;
    if (
      !(await confirm({
        title: t("security.users.resetPassword"),
        description: t("security.users.resetConfirm"),
        tone: "warning",
      }))
    ) {
      return;
    }
    selectedUserManualSelection.current = true;
    setSelectedId(user.user_uuid);
    setActionError("");
    setCopyPasswordError("");
    setResetPasswordError(null);
    setResettingUserId(user.user_uuid);
    try {
      const result = await securityApi.resetPassword(user.user_uuid);
      setUsers((rows) => rows.map((row) => (row.user_uuid === result.user.user_uuid ? result.user : row)));
      if (activeView === "edit" && editingId === result.user.user_uuid) {
        setDraft((current) => ({
          ...current,
          temporaryPassword: result.temporary_password,
        }));
      } else {
        startEdit(result.user, result.temporary_password);
      }
      toast.success(t("security.users.oneTimePassword.resetSuccess"));
    } catch (cause) {
      setResetPasswordError({
        userUuid: user.user_uuid,
        message:
          cause instanceof Error && cause.message.trim()
            ? cause.message
            : t("security.users.oneTimePassword.resetError"),
      });
    } finally {
      setResettingUserId(null);
    }
  };

  const handleUnlock = async (user: SecurityUser) => {
    try {
      const updated = await securityApi.unlockUser(user.user_uuid);
      setUsers((rows) => rows.map((row) => (row.user_uuid === updated.user_uuid ? updated : row)));
      selectedUserManualSelection.current = true;
      setSelectedId(updated.user_uuid);
      toast.success(t("security.common.saved"));
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : t("security.common.saveError"));
    }
  };

  const canDeleteUser = (user: SecurityUser) =>
    user.status === "DISABLED" &&
    !user.is_bootstrap_admin &&
    currentUser?.user_uuid !== user.user_uuid;

  const handleDelete = async (user: SecurityUser) => {
    if (accountActionBusy || busy || !canDeleteUser(user)) return;
    selectedUserManualSelection.current = true;
    setSelectedId(user.user_uuid);
    if (
      !(await confirm({
        title: t("security.users.delete"),
        description: t("security.users.deleteConfirm", {
          name: user.display_name,
          id: user.login_user_id,
        }),
        confirmLabel: t("common.delete"),
        tone: "danger",
        dismissOnOverlay: false,
      }))
    ) {
      return;
    }

    setActionError("");
    setResetPasswordError(null);
    setDeletingUserId(user.user_uuid);
    try {
      await securityApi.deleteUser(user);
      const deletedIndex = filteredUsers.findIndex((row) => row.user_uuid === user.user_uuid);
      const nextUser =
        filteredUsers[deletedIndex + 1] ?? filteredUsers[deletedIndex - 1] ?? null;
      setUsers((rows) => rows.filter((row) => row.user_uuid !== user.user_uuid));
      selectedUserManualSelection.current = Boolean(nextUser);
      setSelectedId(nextUser?.user_uuid ?? null);
      setEditingId(null);
      setActiveView("list");
      setFormError("");
      setFieldErrors({});
      setCopyPasswordError("");
      setResetPasswordError(null);
      toast.success(t("security.users.deleteSuccess", { name: user.display_name }));
    } catch (cause) {
      setActionError(
        cause instanceof Error && cause.message.trim()
          ? cause.message
          : t("security.users.deleteError")
      );
    } finally {
      setDeletingUserId(null);
    }
  };

  const resetPasswordAction = (user: SecurityUser): EntityAction => ({
    id: "reset-password",
    label: t("security.users.resetPassword"),
    icon: KeyRound,
    onSelect: () => handleResetPassword(user),
    visible: user.status === "ACTIVE" && !user.is_bootstrap_admin,
    loading: resettingUserId === user.user_uuid,
    disabled: busy || accountActionBusy,
  });

  const userActions = (user: SecurityUser): EntityAction[] =>
    canManage
      ? [
          {
            id: "edit",
            label: t("security.common.edit"),
            icon: Pencil,
            onSelect: () => startEdit(user),
          },
          resetPasswordAction(user),
          {
            id: "unlock",
            label: t("security.users.unlock"),
            visible: Boolean(user.locked_until),
            disabled: accountActionBusy || busy,
            onSelect: () => handleUnlock(user),
          },
          {
            id: user.status === "ACTIVE" ? "disable" : "enable",
            label: user.status === "ACTIVE" ? t("security.users.disable") : t("security.users.enable"),
            icon: user.status === "ACTIVE" ? UserX : UserCheck,
            tone: user.status === "ACTIVE" ? "danger" : "default",
            loading: statusChangingUserId === user.user_uuid,
            disabled: busy || accountActionBusy,
            onSelect: () => handleToggleStatus(user),
          },
          {
            id: "delete",
            label: t("security.users.delete"),
            icon: Trash2,
            tone: "danger",
            visible: canDeleteUser(user),
            loading: deletingUserId === user.user_uuid,
            disabled: accountActionBusy || busy,
            onSelect: () => handleDelete(user),
          },
        ]
      : [];

  const formUserActions = (...actionIds: string[]) => {
    if (!editingUser || !canManage) return [];
    const actions = userActions(editingUser);
    return actionIds.flatMap((actionId) => {
      const action = actions.find(
        (candidate) => candidate.id === actionId && candidate.visible !== false
      );
      return action ? [entityActionToFormAction(action)] : [];
    });
  };

  const selectRole = (roleId: string) => {
    if (userFormReadOnly) return;
    updateDraftField("selectedRoleId", roleId);
  };

  const userColumns: Array<DataTableColumn<SecurityUser>> = [
    {
      key: "user",
      header: t("security.users.column.user"),
      sortable: true,
      className: "min-w-48",
      render: (user) => {
        const selected = visibleSelectedId === user.user_uuid;
        return (
          <button
            type="button"
            className={`min-w-0 cursor-pointer text-left focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring ${
              selected ? "text-primary" : "text-foreground"
            }`}
            aria-label={t("security.users.showUser", { name: user.display_name })}
            aria-current={selected ? "true" : undefined}
            onClick={(event) => {
              event.stopPropagation();
              selectedUserManualSelection.current = true;
              setSelectedId(user.user_uuid);
            }}
          >
            <span className="block break-words font-medium">{user.display_name}</span>
            <span className="block break-all font-mono text-[11px] text-muted">{user.login_user_id}</span>
          </button>
        );
      },
    },
    {
      key: "roles",
      header: t("security.users.roles"),
      sortable: true,
      className: "min-w-48",
      render: (user) => roleSummary(user),
    },
    {
      key: "status",
      header: t("security.common.status"),
      sortable: true,
      className: "min-w-28",
      render: (user) => <UserStatusBadges user={user} />,
    },
  ];

  const initialLoadFailed = Boolean(loadError) && users.length === 0 && roles.length === 0;

  return (
    <>
      <PageHeader
        title={t("nav.securityUsers")}
        subtitle={t("security.users.subtitle")}
        actions={
          activeView === "list"
            ? [
                ...(canManage && !initialLoadFailed
                  ? [
                      {
                        id: "create-user",
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
        actionsAriaLabel={t("security.users.actionsLabel")}
        actionsTestId="security-users-actions"
      />
      <main className="grid gap-4 p-4 lg:p-8">
        {loadError && !initialLoadFailed ? <Banner severity="danger">{loadError}</Banner> : null}
        {activeView === "list" && actionError ? (
          <Banner severity="danger">{actionError}</Banner>
        ) : null}

        {initialLoadFailed ? (
          <ErrorState message={loadError} onRetry={() => void load()} />
        ) : activeView === "list" ? (
          <SecurityManagementPanelShell
              id="security-users-panel-list"
              idPrefix="security-users"
              ariaLabel={t("security.users.workspaceLabel")}
              splitId="security-users-list"
              preferredWidePane="right"
            >
              <section className="grid min-w-0 content-start gap-3" aria-labelledby="security-users-list-heading">
                <SecurityPanelHeader
                  headingId="security-users-list-heading"
                  icon={Users}
                  title={t("security.users.list")}
                  description={t("security.users.listHint")}
                  action={<StatusBadge variant="info" label={securityFilteredCount(filteredUsers.length, users.length)} />}
                />
                <div className="rounded-md border border-border bg-background p-3">
                  <SecuritySearchField
                    label={t("security.common.search")}
                    placeholder={t("security.users.searchPlaceholder")}
                    value={search}
                    testId="security-users-search"
                    onChange={setSearch}
                  />
                </div>
                {loading ? (
                  <ProcessingIndicator
                    active
                    label={t("security.common.loading")}
                    operationKey="security-users-load"
                    placement="panel"
                    testId="security-users-loading"
                    activityIcon="none"
                  />
                ) : null}
                <MasterDetailDataTable
                  dense
                  loading={loading}
                  rows={filteredUsers}
                  sort={sort}
                  onSortChange={setSort}
                  selectedRowKey={visibleSelectedId}
                  onRowSelect={(user) => {
                    selectedUserManualSelection.current = true;
                    setSelectedId(user.user_uuid);
                  }}
                  getRowKey={(user) => user.user_uuid}
                  getRowAriaLabel={(user) => t("security.users.showUser", { name: user.display_name })}
                  ariaLabel={t("security.users.list")}
                  testId="security-users-grid"
                  scrollAriaLabel={t("security.common.listScrollLabel", {
                    list: t("security.users.list"),
                  })}
                  scrollTestId="security-users-scroll-region"
                  scrollClassName={`${INFORMATION_TABLE_SCROLL_CLASS} ${INFORMATION_TABLE_FOCUS_CLASS}`}
                  className="[&_thead]:sticky [&_thead]:top-0 [&_thead]:z-10 [&_thead_tr]:h-10"
                  rowClassName={INFORMATION_TABLE_ROW_CLASS}
                  empty={<EmptyState title={search ? t("security.users.noResultsTitle") : t("security.common.empty")} hint={search ? t("security.users.noResultsHint") : undefined} />}
                  columns={userColumns}
                />
              </section>

              <UserDetailPanel
                user={selectedUser}
                canManage={canManage}
                assignedRoles={selectedUser ? assignedRoles(selectedUser) : []}
                actions={selectedUser ? userActions(selectedUser) : []}
                resetError={
                  selectedUser && resetPasswordError?.userUuid === selectedUser.user_uuid
                    ? resetPasswordError.message
                    : ""
                }
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
              id={`security-users-panel-${activeView}`}
              idPrefix="security-users"
              ariaLabel={t("security.users.taskPanelLabel")}
            >
              <SecurityPanelHeader
                icon={activeView === "create" ? Plus : Pencil}
                title={activeView === "edit" ? t("security.users.form.edit") : t("security.users.form.create")}
                description={t("security.users.formHint")}
                headingId="security-users-form-heading"
              />
              <form
                ref={formRef}
                className="grid gap-4"
                onSubmit={handleSubmit}
                aria-labelledby="security-users-form-heading"
              >
                    <RequiredFieldsNote />
                    <div className="grid gap-4 lg:grid-cols-2">
                  <div className="grid gap-1.5 text-sm font-medium">
                    <FieldLabel htmlFor="security-user-login-user-id" label={t("security.users.loginUserId")} required />
                    <input
                      ref={loginUserIdRef}
                      id="security-user-login-user-id"
                      required
                      maxLength={64}
                      disabled={activeView === "edit"}
                      className={cn(INPUT_CLASS, fieldErrors.loginUserId && "border-danger")}
                      aria-invalid={fieldErrors.loginUserId ? "true" : undefined}
                      aria-describedby={fieldErrors.loginUserId ? "security-user-login-user-id-error" : undefined}
                      autoComplete="off"
                      value={draft.loginUserId}
                      onChange={(event) => updateDraftField("loginUserId", event.target.value)}
                    />
                    <FieldError id="security-user-login-user-id-error" message={fieldErrors.loginUserId} />
                  </div>
                  <div className="grid gap-1.5 text-sm font-medium">
                    <FieldLabel htmlFor="security-user-display-name" label={t("security.users.displayName")} required />
                    <input
                      ref={displayNameRef}
                      id="security-user-display-name"
                      required
                      disabled={userFormReadOnly}
                      className={cn(INPUT_CLASS, fieldErrors.displayName && "border-danger")}
                      aria-invalid={fieldErrors.displayName ? "true" : undefined}
                      aria-describedby={fieldErrors.displayName ? "security-user-display-name-error" : undefined}
                      value={draft.displayName}
                      onChange={(event) => updateDraftField("displayName", event.target.value)}
                    />
                    <FieldError id="security-user-display-name-error" message={fieldErrors.displayName} />
                  </div>
                    </div>
                    <div className="grid gap-1.5 text-sm font-medium">
                      <FieldLabel
                        htmlFor="security-user-temporary-password"
                        label={t(
                          activeView === "create"
                            ? "security.users.tempPassword"
                            : "security.users.oneTimePassword.valueLabel"
                        )}
                      />
                      <div
                        className={cn(
                          "grid min-w-0 gap-2",
                          activeView === "edit" &&
                            "sm:grid-cols-[minmax(0,1fr)_auto] sm:items-start"
                        )}
                      >
                        <input
                          ref={temporaryPasswordRef}
                          id="security-user-temporary-password"
                          type={activeView === "create" ? "password" : "text"}
                          readOnly={activeView === "edit"}
                          disabled={userFormReadOnly}
                          className={cn(INPUT_CLASS, fieldErrors.temporaryPassword && "border-danger")}
                          aria-invalid={fieldErrors.temporaryPassword ? "true" : undefined}
                          aria-describedby={
                            fieldErrors.temporaryPassword
                              ? "security-user-temporary-password-error"
                              : activeView === "edit" &&
                                  (copyPasswordError ||
                                    (resetPasswordError?.userUuid === editingUser?.user_uuid &&
                                      resetPasswordError?.message))
                                ? "security-user-temporary-password-action-error"
                                : undefined
                          }
                          autoComplete={activeView === "create" ? "new-password" : "off"}
                          value={draft.temporaryPassword}
                          onChange={(event) => {
                            if (activeView === "create") {
                              updateDraftField("temporaryPassword", event.target.value);
                            }
                          }}
                          data-testid="security-user-temporary-password"
                        />
                        {activeView === "edit" ? (
                          <Button
                            type="button"
                            variant="secondary"
                            className="w-full sm:w-auto"
                            disabled={userFormReadOnly || !draft.temporaryPassword}
                            onClick={() => void copyTemporaryPassword()}
                            data-testid="security-user-temporary-password-copy"
                          >
                            <Copy size={15} aria-hidden="true" />
                            <span>{t("security.users.oneTimePassword.copy")}</span>
                          </Button>
                        ) : null}
                      </div>
                      <FieldError
                        id="security-user-temporary-password-error"
                        message={fieldErrors.temporaryPassword}
                      />
                      {activeView === "edit" ? (
                        <div
                          id="security-user-temporary-password-action-error"
                          data-testid="security-user-temporary-password-error"
                        >
                          <FormStatus
                            tone="danger"
                            message={
                              copyPasswordError ||
                              (resetPasswordError?.userUuid === editingUser?.user_uuid
                                ? (resetPasswordError?.message ?? "")
                                : "")
                            }
                          />
                        </div>
                      ) : null}
                    </div>
                    <fieldset className="grid gap-2" disabled={userFormReadOnly}>
                  <FieldLegend id="security-users-role-legend" required>{t("security.users.roles")}</FieldLegend>
                  {roles.length === 0 ? (
                    <p className="text-sm text-muted">{t("security.users.noRole")}</p>
                  ) : (
                    <div
                      ref={roleGroupRef}
                      className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3"
                      role="radiogroup"
                      aria-labelledby="security-users-role-legend"
                      aria-required="true"
                      aria-invalid={fieldErrors.selectedRoleId ? "true" : undefined}
                      aria-describedby={fieldErrors.selectedRoleId ? "security-users-role-error" : undefined}
                    >
                      {roles.map((role) => {
                        const disabled = userFormReadOnly || isSystemAdminRoleDisabled(role);
                        const hint = systemAdminRoleHint(role);
                        const selected = draft.selectedRoleId === role.role_id;
                        return (
                          <label
                            key={role.role_id}
                            className={cn(
                              "flex min-h-11 items-start gap-2 rounded-md border p-2.5 text-sm transition-colors",
                              disabled
                                ? "cursor-not-allowed bg-muted/20 text-muted"
                                : selected
                                  ? "cursor-pointer border-primary bg-info-bg/40"
                                  : "cursor-pointer border-border hover:bg-background"
                            )}
                          >
                            <input
                              className="mt-0.5 h-4 w-4 accent-primary disabled:cursor-not-allowed"
                              type="radio"
                              name="security-users-role"
                              value={role.role_id}
                              checked={selected}
                              disabled={disabled}
                              onChange={() => selectRole(role.role_id)}
                            />
                            <span className="min-w-0">
                              <span className="block break-words font-medium">{role.display_name}</span>
                              <span className="block break-all font-mono text-[11px] text-muted">{role.role_code}</span>
                              {hint ? <span className="mt-1 block text-xs leading-5 text-muted">{hint}</span> : null}
                            </span>
                          </label>
                        );
                      })}
                    </div>
                  )}
                  <FieldError id="security-users-role-error" message={fieldErrors.selectedRoleId} />
                    </fieldset>
                    <FormActionBar
                      ariaLabel={t(
                        activeView === "edit"
                          ? "security.users.editActions"
                          : "security.users.createActions"
                      )}
                      testId="security-users-form-actions"
                      primaryActions={
                        canSubmitUserForm
                          ? [
                              {
                                id: activeView === "edit" ? "save" : "create",
                                label:
                                  activeView === "edit"
                                    ? t("security.common.save")
                                    : t("security.common.create"),
                                loading: busy,
                                disabled: accountActionBusy,
                                onClick: () => formRef.current?.requestSubmit(),
                              },
                            ]
                          : []
                      }
                      secondaryActions={[
                        ...(activeView === "edit"
                          ? formUserActions("reset-password", "enable")
                          : []),
                        {
                          id: "cancel",
                          label: t("security.common.cancel"),
                          disabled: busy || accountActionBusy,
                          onClick: returnToList,
                        },
                      ]}
                      dangerActions={
                        activeView === "edit"
                          ? formUserActions("disable", "delete")
                          : []
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

function UserStatusBadges({ user }: { user: SecurityUser }) {
  return (
    <div className="flex flex-wrap gap-1">
      <StatusBadge
        variant={user.status === "ACTIVE" ? "success" : "neutral"}
        label={userStatusLabel(user)}
      />
      {user.locked_until ? <StatusBadge variant="warning" label={t("security.users.locked")} /> : null}
    </div>
  );
}

function UserDetailPanel({
  user,
  assignedRoles,
  canManage,
  actions,
  resetError,
}: {
  user: SecurityUser | null;
  assignedRoles: AssignedRole[];
  canManage: boolean;
  actions: EntityAction[];
  resetError: string;
}) {
  if (!user) {
    return (
      <SecurityEmptySelection
        title={t("security.users.noSelectionTitle")}
        hint={t("security.users.noSelectionHint")}
      />
    );
  }

  const hasArchivedRole = assignedRoles.some((role) => role.archived);

  return (
    <section className="grid min-w-0 content-start gap-4 rounded-md border border-border bg-background p-4" aria-labelledby="security-users-detail-heading">
      <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 id="security-users-detail-heading" className="flex min-w-0 items-center gap-2 text-base font-semibold text-foreground">
              <UserRound size={18} aria-hidden="true" />
              <span className="min-w-0 break-words">{user.display_name}</span>
            </h2>
            <UserStatusBadges user={user} />
          </div>
          <p className="mt-1 break-all font-mono text-xs text-muted">{user.login_user_id}</p>
        </div>
        {canManage ? (
          <ObjectActionBar
            actions={actions}
            ariaLabel={`${t("security.common.actions")}: ${user.display_name}`}
            testId="security-users-detail-actions"
          />
        ) : null}
      </div>
      <div data-testid="security-users-reset-password-error">
        <FormStatus tone="danger" message={resetError} />
      </div>
      <dl className="grid gap-3 md:grid-cols-2">
        <SecurityDetailField label={t("security.users.loginUserId")}>
          <code className="break-all font-mono text-xs">{user.login_user_id}</code>
        </SecurityDetailField>
        <SecurityDetailField label={t("security.common.status")}>
          <UserStatusBadges user={user} />
        </SecurityDetailField>
        <SecurityDetailField label={t("security.users.forceChange")}>
          {user.force_password_change ? t("security.common.yes") : t("security.common.no")}
        </SecurityDetailField>
        <SecurityDetailField label={t("security.common.version")}>
          {String(user.version)}
        </SecurityDetailField>
      </dl>

      <section className="grid gap-2" aria-label={t("security.users.roles")}>
        <h3 className="text-sm font-semibold text-foreground">{t("security.users.roles")}</h3>
        {hasArchivedRole ? (
          <Banner severity="warning">{t("security.users.archivedRoleNotice")}</Banner>
        ) : null}
        {assignedRoles.length > 0 ? (
          <div className="flex flex-wrap gap-1.5">
            {assignedRoles.map((role) => (
              <StatusBadge
                key={role.role_id}
                variant={role.archived ? "neutral" : "info"}
                label={
                  role.archived
                    ? t("security.users.archivedRoleLabel", { role: role.display_name })
                    : role.display_name
                }
              />
            ))}
          </div>
        ) : (
          <p className="rounded-md border border-dashed border-border p-3 text-sm text-muted">{t("security.common.none")}</p>
        )}
      </section>
    </section>
  );
}
