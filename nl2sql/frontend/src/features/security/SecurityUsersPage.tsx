import { useEffect, useMemo, useRef, useState, type FormEvent, type MouseEvent } from "react";
import {
  ArrowLeft,
  KeyRound,
  Pencil,
  Plus,
  RefreshCw,
  UserCheck,
  UserRound,
  Users,
  UserX,
} from "lucide-react";

import {
  Banner,
  Button,
  DataTable,
  EmptyState,
  FormStatus,
  PageHeader,
  StatusBadge,
  toast,
  type DataTableColumn,
  type DataTableSort,
} from "@engchina/production-ready-ui";

import { useConfirm } from "@/components/ui/confirm-dialog";
import { isAbortError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { t } from "@/lib/i18n";
import { useRequestScope } from "@/lib/useRequestScope";
import { useAuth } from "./AuthProvider";
import {
  SecurityDetailField,
  SecurityEmptySelection,
  SecurityManagementPanelShell,
  SecurityManagementStatusBar,
  SecurityPanelHeader,
  SecuritySearchField,
  securityFilteredCount,
} from "./SecurityManagementShared";
import { securityApi } from "./api";
import type { SecurityRole, SecurityUser } from "./types";

type UserPanelView = "list" | "create" | "edit";

interface UserDraftState {
  loginName: string;
  displayName: string;
  roleIds: string[];
  temporaryPassword: string;
}

const EMPTY_DRAFT: UserDraftState = {
  loginName: "",
  displayName: "",
  roleIds: [],
  temporaryPassword: "",
};

const INPUT_CLASS =
  "h-11 w-full rounded-md border border-border bg-card px-3 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-ring/30 disabled:bg-muted/20 disabled:text-muted";

function compareText(left: string, right: string, direction: DataTableSort["direction"]) {
  const result = left.localeCompare(right, "ja");
  return direction === "asc" ? result : -result;
}

function userStatusLabel(user: SecurityUser) {
  return user.status === "ACTIVE" ? t("security.common.active") : t("security.common.disabled");
}

export function SecurityUsersPage() {
  const confirm = useConfirm();
  const { hasPermission } = useAuth();
  const canManage = hasPermission("security.users.manage");
  const [users, setUsers] = useState<SecurityUser[]>([]);
  const [roles, setRoles] = useState<SecurityRole[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [formError, setFormError] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [activeView, setActiveView] = useState<UserPanelView>("list");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<DataTableSort>({ key: "user", direction: "asc" });
  const [draft, setDraft] = useState<UserDraftState>(EMPTY_DRAFT);
  const [oneTimePassword, setOneTimePassword] = useState("");
  const loadSequence = useRef(0);
  const { abortAll, run: runScopedRequest } = useRequestScope();

  const roleNameById = useMemo(
    () => new Map(roles.map((role) => [role.role_id, role.display_name])),
    [roles]
  );

  const selectedUser = users.find((user) => user.user_id === selectedId) ?? null;
  const editingUser = users.find((user) => user.user_id === editingId) ?? null;
  const activeUsers = users.filter((user) => user.status === "ACTIVE").length;
  const lockedUsers = users.filter((user) => Boolean(user.locked_until)).length;

  const roleNames = (user: SecurityUser) =>
    user.role_ids.map((id) => roleNameById.get(id) ?? id).filter(Boolean);

  const roleSummary = (user: SecurityUser) => {
    const names = roleNames(user);
    return names.length > 0 ? names.join(", ") : t("security.common.none");
  };

  const filteredUsers = useMemo(() => {
    const q = search.trim().toLowerCase();
    return users
      .filter((user) => {
        if (!q) return true;
        return (
          user.display_name.toLowerCase().includes(q) ||
          user.login_name.toLowerCase().includes(q) ||
          user.status.toLowerCase().includes(q) ||
          roleSummary(user).toLowerCase().includes(q)
        );
      })
      .sort((left, right) => {
        if (sort.key === "login") return compareText(left.login_name, right.login_name, sort.direction);
        if (sort.key === "roles") return compareText(roleSummary(left), roleSummary(right), sort.direction);
        if (sort.key === "status") return compareText(userStatusLabel(left), userStatusLabel(right), sort.direction);
        return compareText(left.display_name, right.display_name, sort.direction);
      });
  }, [roleNameById, search, sort, users]);

  const load = async () => {
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
          current && userRows.some((user) => user.user_id === current)
            ? current
            : userRows[0]?.user_id ?? null
        );
      });
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

  const startCreate = () => {
    setActiveView("create");
    setEditingId(null);
    setDraft(EMPTY_DRAFT);
    setFormError("");
    setOneTimePassword("");
  };

  const startEdit = (user: SecurityUser) => {
    setSelectedId(user.user_id);
    setEditingId(user.user_id);
    setActiveView("edit");
    setDraft({
      loginName: user.login_name,
      displayName: user.display_name,
      roleIds: user.role_ids,
      temporaryPassword: "",
    });
    setFormError("");
    setOneTimePassword("");
  };

  const returnToList = () => {
    setActiveView("list");
    setEditingId(null);
    setFormError("");
    setOneTimePassword("");
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setFormError("");
    setOneTimePassword("");
    try {
      if (activeView === "edit") {
        if (!editingUser) return;
        const updated = await securityApi.updateUser({
          ...editingUser,
          display_name: draft.displayName,
          role_ids: draft.roleIds,
        });
        setUsers((rows) => rows.map((row) => (row.user_id === updated.user_id ? updated : row)));
        setSelectedId(updated.user_id);
        setDraft((current) => ({ ...current, displayName: updated.display_name, roleIds: updated.role_ids }));
        toast.success(t("security.common.saved"));
      } else {
        const created = await securityApi.createUser({
          login_name: draft.loginName,
          display_name: draft.displayName,
          role_ids: draft.roleIds,
          temporary_password: draft.temporaryPassword || undefined,
        });
        setUsers((rows) => [...rows, created.user]);
        setSelectedId(created.user.user_id);
        setEditingId(created.user.user_id);
        setActiveView("edit");
        setOneTimePassword(created.temporary_password);
        setDraft({
          loginName: created.user.login_name,
          displayName: created.user.display_name,
          roleIds: created.user.role_ids,
          temporaryPassword: "",
        });
        toast.success(t("security.common.saved"));
      }
    } catch (cause) {
      setFormError(cause instanceof Error ? cause.message : t("security.common.saveError"));
    } finally {
      setBusy(false);
    }
  };

  const handleToggleStatus = async (user: SecurityUser) => {
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
    try {
      const updated = await securityApi.setUserEnabled(user, enabling);
      setUsers((rows) => rows.map((row) => (row.user_id === updated.user_id ? updated : row)));
      setSelectedId(updated.user_id);
      toast.success(t("security.common.saved"));
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : t("security.common.saveError"));
    }
  };

  const handleResetPassword = async (user: SecurityUser) => {
    if (
      !(await confirm({
        title: t("security.users.resetPassword"),
        description: t("security.users.resetConfirm"),
        tone: "warning",
      }))
    ) {
      return;
    }
    try {
      const result = await securityApi.resetPassword(user.user_id);
      setUsers((rows) => rows.map((row) => (row.user_id === result.user.user_id ? result.user : row)));
      startEdit(result.user);
      setOneTimePassword(result.temporary_password);
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : t("security.common.saveError"));
    }
  };

  const handleUnlock = async (user: SecurityUser) => {
    try {
      const updated = await securityApi.unlockUser(user.user_id);
      setUsers((rows) => rows.map((row) => (row.user_id === updated.user_id ? updated : row)));
      setSelectedId(updated.user_id);
      toast.success(t("security.common.saved"));
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : t("security.common.saveError"));
    }
  };

  const toggleRole = (roleId: string) => {
    setDraft((current) => ({
      ...current,
      roleIds: current.roleIds.includes(roleId)
        ? current.roleIds.filter((id) => id !== roleId)
        : [...current.roleIds, roleId],
    }));
  };

  const userColumns: Array<DataTableColumn<SecurityUser>> = [
    {
      key: "user",
      header: t("security.users.column.user"),
      sortable: true,
      className: "min-w-48",
      render: (user) => {
        const selected = selectedId === user.user_id;
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
              setSelectedId(user.user_id);
            }}
          >
            <span className="block break-words font-medium">{user.display_name}</span>
            <span className="block break-all font-mono text-[11px] text-muted">{user.login_name}</span>
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
    ...(canManage
      ? [
          {
            key: "actions",
            header: t("security.common.actions"),
            className: "min-w-64",
            render: (user: SecurityUser) => (
              <div className="flex flex-wrap justify-end gap-1">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={(event: MouseEvent<HTMLButtonElement>) => {
                    event.stopPropagation();
                    startEdit(user);
                  }}
                >
                  <Pencil size={14} aria-hidden />
                  {t("security.common.edit")}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={(event: MouseEvent<HTMLButtonElement>) => {
                    event.stopPropagation();
                    void handleResetPassword(user);
                  }}
                >
                  <KeyRound size={14} aria-hidden />
                  {t("security.users.resetPassword")}
                </Button>
                {user.locked_until ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={(event: MouseEvent<HTMLButtonElement>) => {
                      event.stopPropagation();
                      void handleUnlock(user);
                    }}
                  >
                    {t("security.users.unlock")}
                  </Button>
                ) : null}
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={(event: MouseEvent<HTMLButtonElement>) => {
                    event.stopPropagation();
                    void handleToggleStatus(user);
                  }}
                >
                  {user.status === "ACTIVE" ? <UserX size={14} aria-hidden /> : <UserCheck size={14} aria-hidden />}
                  {user.status === "ACTIVE" ? t("security.users.disable") : t("security.users.enable")}
                </Button>
              </div>
            ),
          },
        ]
      : []),
  ];

  return (
    <>
      <PageHeader
        className="px-4 sm:px-8"
        title={t("nav.securityUsers")}
        subtitle={t("security.users.subtitle")}
      />
      <main className="grid gap-4 p-4 lg:p-8">
        {loadError ? <Banner severity="danger">{loadError}</Banner> : null}
        {actionError ? <Banner severity="danger">{actionError}</Banner> : null}

        <SecurityManagementStatusBar
          ariaLabel={t("security.users.statusBar")}
          metrics={[
            { label: t("security.users.metric.total"), value: String(users.length), emphasis: true, testId: "security-users-metric-total" },
            { label: t("security.users.metric.active"), value: String(activeUsers), testId: "security-users-metric-active" },
            { label: t("security.users.metric.locked"), value: String(lockedUsers), testId: "security-users-metric-locked" },
          ]}
          actions={
            <Button type="button" variant="secondary" size="sm" onClick={() => void load()} disabled={loading}>
              <RefreshCw size={15} aria-hidden="true" />
              <span>{t("security.common.reload")}</span>
            </Button>
          }
        />

        {activeView === "list" ? (
          <>
            {canManage ? (
              <div
                className="flex flex-wrap items-center justify-end gap-2"
                data-testid="security-users-actions"
                aria-label={t("security.users.actionsLabel")}
              >
                <Button type="button" size="sm" onClick={startCreate}>
                  <Plus size={15} aria-hidden="true" />
                  <span>{t("security.common.create")}</span>
                </Button>
              </div>
            ) : null}
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
                <DataTable
                  dense
                  loading={loading}
                  rows={filteredUsers}
                  sort={sort}
                  onSortChange={setSort}
                  onRowClick={(user) => setSelectedId(user.user_id)}
                  getRowKey={(user) => user.user_id}
                  ariaLabel={t("security.users.list")}
                  testId="security-users-grid"
                  empty={<EmptyState title={search ? t("security.users.noResultsTitle") : t("security.common.empty")} hint={search ? t("security.users.noResultsHint") : undefined} />}
                  columns={userColumns}
                />
              </section>

              <UserDetailPanel
                user={selectedUser}
                canManage={canManage}
                roleNames={selectedUser ? roleNames(selectedUser) : []}
                onEdit={startEdit}
                onResetPassword={(user) => void handleResetPassword(user)}
                onUnlock={(user) => void handleUnlock(user)}
                onToggleStatus={(user) => void handleToggleStatus(user)}
              />
            </SecurityManagementPanelShell>
          </>
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
              <form className="grid gap-4" onSubmit={handleSubmit} aria-labelledby="security-users-form-heading">
                {oneTimePassword ? (
                  <Banner severity="warning" title={t("security.users.oneTimePassword")}>
                    <code className="mt-2 block select-all break-all rounded bg-background p-2 font-mono text-sm">
                      {oneTimePassword}
                    </code>
                  </Banner>
                ) : null}
                <div className="grid gap-4 lg:grid-cols-2">
                  <label className="grid gap-1.5 text-sm font-medium">
                    <span>{t("security.users.loginName")}</span>
                    <input
                      required
                      disabled={activeView === "edit"}
                      className={INPUT_CLASS}
                      autoComplete="off"
                      value={draft.loginName}
                      onChange={(event) => setDraft((current) => ({ ...current, loginName: event.target.value }))}
                    />
                  </label>
                  <label className="grid gap-1.5 text-sm font-medium">
                    <span>{t("security.users.displayName")}</span>
                    <input
                      required
                      className={INPUT_CLASS}
                      value={draft.displayName}
                      onChange={(event) => setDraft((current) => ({ ...current, displayName: event.target.value }))}
                    />
                  </label>
                </div>
                {activeView === "create" ? (
                  <label className="grid gap-1.5 text-sm font-medium">
                    <span>{t("security.users.tempPassword")}</span>
                    <input
                      type="password"
                      className={INPUT_CLASS}
                      autoComplete="new-password"
                      value={draft.temporaryPassword}
                      onChange={(event) => setDraft((current) => ({ ...current, temporaryPassword: event.target.value }))}
                    />
                  </label>
                ) : null}
                <fieldset className="grid gap-2">
                  <legend className="text-sm font-semibold">{t("security.users.roles")}</legend>
                  {roles.length === 0 ? (
                    <p className="text-sm text-muted">{t("security.users.noRole")}</p>
                  ) : (
                    <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                      {roles.map((role) => (
                        <label
                          key={role.role_id}
                          className="flex min-h-11 cursor-pointer items-start gap-2 rounded-md border border-border p-2.5 text-sm hover:bg-background"
                        >
                          <input
                            className="mt-0.5 h-4 w-4 accent-primary"
                            type="checkbox"
                            checked={draft.roleIds.includes(role.role_id)}
                            onChange={() => toggleRole(role.role_id)}
                          />
                          <span className="min-w-0">
                            <span className="block break-words font-medium">{role.display_name}</span>
                            <span className="block break-all font-mono text-[11px] text-muted">{role.role_code}</span>
                          </span>
                        </label>
                      ))}
                    </div>
                  )}
                </fieldset>
                <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
                  <Button loading={busy} type="submit">
                    {activeView === "edit" ? t("security.common.save") : t("security.common.create")}
                  </Button>
                  <Button type="button" variant="secondary" onClick={returnToList}>
                    {t("security.common.cancel")}
                  </Button>
                  <FormStatus tone="danger" message={formError} className="w-full" />
                </div>
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
  roleNames,
  canManage,
  onEdit,
  onResetPassword,
  onUnlock,
  onToggleStatus,
}: {
  user: SecurityUser | null;
  roleNames: string[];
  canManage: boolean;
  onEdit: (user: SecurityUser) => void;
  onResetPassword: (user: SecurityUser) => void;
  onUnlock: (user: SecurityUser) => void;
  onToggleStatus: (user: SecurityUser) => void;
}) {
  if (!user) {
    return (
      <SecurityEmptySelection
        title={t("security.users.noSelectionTitle")}
        hint={t("security.users.noSelectionHint")}
      />
    );
  }

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
          <p className="mt-1 break-all font-mono text-xs text-muted">{user.login_name}</p>
        </div>
        {canManage ? (
          <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap xl:justify-end">
            <Button type="button" variant="secondary" size="sm" onClick={() => onEdit(user)}>
              <Pencil size={15} aria-hidden="true" />
              <span>{t("security.common.edit")}</span>
            </Button>
            <Button type="button" variant="secondary" size="sm" onClick={() => onResetPassword(user)}>
              <KeyRound size={15} aria-hidden="true" />
              <span>{t("security.users.resetPassword")}</span>
            </Button>
            {user.locked_until ? (
              <Button type="button" variant="secondary" size="sm" onClick={() => onUnlock(user)}>
                <span>{t("security.users.unlock")}</span>
              </Button>
            ) : null}
            <Button type="button" variant="secondary" size="sm" onClick={() => onToggleStatus(user)}>
              {user.status === "ACTIVE" ? <UserX size={15} aria-hidden="true" /> : <UserCheck size={15} aria-hidden="true" />}
              <span>{user.status === "ACTIVE" ? t("security.users.disable") : t("security.users.enable")}</span>
            </Button>
          </div>
        ) : null}
      </div>

      <dl className="grid gap-3 md:grid-cols-2">
        <SecurityDetailField label={t("security.users.loginName")}>
          <code className="break-all font-mono text-xs">{user.login_name}</code>
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
        <SecurityDetailField label={t("security.users.lockedUntil")}>
          {user.locked_until ? formatDateTime(user.locked_until) : t("security.common.none")}
        </SecurityDetailField>
      </dl>

      <section className="grid gap-2" aria-label={t("security.users.roles")}>
        <h3 className="text-sm font-semibold text-foreground">{t("security.users.roles")}</h3>
        {roleNames.length > 0 ? (
          <div className="flex flex-wrap gap-1.5">
            {roleNames.map((roleName) => (
              <StatusBadge key={roleName} variant="info" label={roleName} />
            ))}
          </div>
        ) : (
          <p className="rounded-md border border-dashed border-border p-3 text-sm text-muted">{t("security.common.none")}</p>
        )}
      </section>
    </section>
  );
}
