import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { KeyRound, Pencil, Plus, RefreshCw, UserCheck, UserX } from "lucide-react";

import {
  Banner,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  DataTable,
  EmptyState,
  FormStatus,
  PageHeader,
  StatusBadge,
  toast,
} from "@engchina/production-ready-ui";

import { useConfirm } from "@/components/ui/confirm-dialog";
import { t } from "@/lib/i18n";
import { useAuth } from "./AuthProvider";
import { securityApi } from "./api";
import type { SecurityRole, SecurityUser } from "./types";

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
  "h-10 w-full rounded-md border border-border bg-card px-3 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-ring/30 disabled:bg-muted/20 disabled:text-muted";

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
  const [draft, setDraft] = useState<UserDraftState>(EMPTY_DRAFT);
  const [oneTimePassword, setOneTimePassword] = useState("");
  const loadSequence = useRef(0);

  const roleNameById = useMemo(
    () => new Map(roles.map((role) => [role.role_id, role.display_name])),
    [roles]
  );

  const load = async () => {
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    setLoading(true);
    setLoadError("");
    setActionError("");
    try {
      const [userRows, roleRows] = await Promise.all([securityApi.users(), securityApi.roles()]);
      if (sequence === loadSequence.current) {
        setUsers(userRows);
        setRoles(roleRows.filter((role) => !role.archived));
      }
    } catch (cause) {
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
  }, []);

  const startCreate = () => {
    setSelectedId(null);
    setDraft(EMPTY_DRAFT);
    setFormError("");
    setOneTimePassword("");
  };

  const startEdit = (user: SecurityUser) => {
    setSelectedId(user.user_id);
    setDraft({
      loginName: user.login_name,
      displayName: user.display_name,
      roleIds: user.role_ids,
      temporaryPassword: "",
    });
    setFormError("");
    setOneTimePassword("");
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setFormError("");
    setOneTimePassword("");
    try {
      if (selectedId) {
        const current = users.find((user) => user.user_id === selectedId);
        if (!current) return;
        const updated = await securityApi.updateUser({
          ...current,
          display_name: draft.displayName,
          role_ids: draft.roleIds,
        });
        setUsers((rows) => rows.map((row) => (row.user_id === updated.user_id ? updated : row)));
        toast.success(t("security.common.saved"));
      } else {
        const created = await securityApi.createUser({
          login_name: draft.loginName,
          display_name: draft.displayName,
          role_ids: draft.roleIds,
          temporary_password: draft.temporaryPassword || undefined,
        });
        setUsers((rows) => [...rows, created.user]);
        setOneTimePassword(created.temporary_password);
        setSelectedId(created.user.user_id);
        setDraft((current) => ({ ...current, loginName: created.user.login_name }));
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
      setOneTimePassword(result.temporary_password);
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

  return (
    <>
      <PageHeader
        className="px-4 sm:px-8"
        title={t("nav.securityUsers")}
        subtitle={t("security.users.subtitle")}
        actions={
          <Button variant="secondary" size="sm" onClick={() => void load()} disabled={loading}>
            <RefreshCw size={14} aria-hidden />
            {t("security.common.reload")}
          </Button>
        }
      />
      <main
        className={`grid gap-5 p-4 xl:p-8 ${
          canManage ? "xl:grid-cols-[minmax(0,1.35fr)_minmax(22rem,0.65fr)]" : "grid-cols-1"
        }`}
      >
        {loadError ? (
          <Banner severity="danger" className={canManage ? "xl:col-span-2" : undefined}>
            {loadError}
          </Banner>
        ) : null}
        <section className="space-y-4" aria-labelledby="security-user-list-title">
          {actionError ? <Banner severity="danger">{actionError}</Banner> : null}
          <Card>
            <CardHeader className="flex-row items-center justify-between gap-3">
              <CardTitle id="security-user-list-title">{t("security.users.list")}</CardTitle>
              {canManage ? (
                <Button size="sm" onClick={startCreate}>
                  <Plus size={14} aria-hidden />
                  {t("security.common.create")}
                </Button>
              ) : null}
            </CardHeader>
            <CardContent>
              <DataTable
                loading={loading}
                rows={users}
                getRowKey={(user) => user.user_id}
                ariaLabel={t("security.users.list")}
                empty={<EmptyState title={t("security.common.empty")} />}
                columns={[
                  {
                    key: "user",
                    header: t("security.users.displayName"),
                    render: (user) => (
                      <div>
                        <p className="font-medium">{user.display_name}</p>
                        <p className="font-mono text-[11px] text-muted">{user.login_name}</p>
                      </div>
                    ),
                  },
                  {
                    key: "roles",
                    header: t("security.users.roles"),
                    render: (user) =>
                      user.role_ids.map((id) => roleNameById.get(id)).filter(Boolean).join(", ") ||
                      t("security.common.none"),
                  },
                  {
                    key: "status",
                    header: t("security.common.status"),
                    render: (user) =>
                      canManage ? (
                        <div className="flex flex-wrap gap-1">
                        <StatusBadge
                          variant={user.status === "ACTIVE" ? "success" : "neutral"}
                          label={
                            user.status === "ACTIVE"
                              ? t("security.common.active")
                              : t("security.common.disabled")
                          }
                        />
                        {user.locked_until ? (
                          <StatusBadge variant="warning" label={t("security.users.locked")} />
                        ) : null}
                        </div>
                      ) : null,
                  },
                  {
                    key: "actions",
                    header: t("security.common.actions"),
                    className: "min-w-48",
                    render: (user) => (
                      <div className="flex flex-wrap gap-1">
                        <Button size="sm" variant="ghost" onClick={() => startEdit(user)}>
                          <Pencil size={14} aria-hidden />
                          {t("security.common.edit")}
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => void handleResetPassword(user)}>
                          <KeyRound size={14} aria-hidden />
                          {t("security.users.resetPassword")}
                        </Button>
                        {user.locked_until ? (
                          <Button size="sm" variant="ghost" onClick={() => void handleUnlock(user)}>
                            {t("security.users.unlock")}
                          </Button>
                        ) : null}
                        <Button size="sm" variant="ghost" onClick={() => void handleToggleStatus(user)}>
                          {user.status === "ACTIVE" ? <UserX size={14} aria-hidden /> : <UserCheck size={14} aria-hidden />}
                          {user.status === "ACTIVE" ? t("security.users.disable") : t("security.users.enable")}
                        </Button>
                      </div>
                    ),
                  },
                ]}
              />
            </CardContent>
          </Card>
        </section>

        {canManage ? <Card className="h-fit">
          <CardHeader>
            <CardTitle>
              {selectedId ? t("security.users.form.edit") : t("security.users.form.create")}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <form className="space-y-4" onSubmit={handleSubmit}>
              {oneTimePassword ? (
                <Banner severity="warning" title={t("security.users.oneTimePassword")}>
                  <code className="mt-2 block select-all break-all rounded bg-background p-2 font-mono text-sm">
                    {oneTimePassword}
                  </code>
                </Banner>
              ) : null}
              <label className="block space-y-1.5 text-sm font-medium">
                <span>{t("security.users.loginName")}</span>
                <input
                  required
                  disabled={Boolean(selectedId)}
                  className={INPUT_CLASS}
                  autoComplete="off"
                  value={draft.loginName}
                  onChange={(event) => setDraft((current) => ({ ...current, loginName: event.target.value }))}
                />
              </label>
              <label className="block space-y-1.5 text-sm font-medium">
                <span>{t("security.users.displayName")}</span>
                <input
                  required
                  className={INPUT_CLASS}
                  value={draft.displayName}
                  onChange={(event) => setDraft((current) => ({ ...current, displayName: event.target.value }))}
                />
              </label>
              {!selectedId ? (
                <label className="block space-y-1.5 text-sm font-medium">
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
              <fieldset className="space-y-2">
                <legend className="text-sm font-medium">{t("security.users.roles")}</legend>
                {roles.length === 0 ? (
                  <p className="text-sm text-muted">{t("security.users.noRole")}</p>
                ) : (
                  <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-1">
                    {roles.map((role) => (
                      <label key={role.role_id} className="flex cursor-pointer items-start gap-2 rounded-md border border-border p-2.5 text-sm hover:bg-background">
                        <input
                          className="mt-0.5 h-4 w-4 accent-primary"
                          type="checkbox"
                          checked={draft.roleIds.includes(role.role_id)}
                          onChange={() => toggleRole(role.role_id)}
                        />
                        <span>
                          <span className="block font-medium">{role.display_name}</span>
                          <span className="font-mono text-[11px] text-muted">{role.role_code}</span>
                        </span>
                      </label>
                    ))}
                  </div>
                )}
              </fieldset>
              <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
                <Button loading={busy} type="submit">
                  {selectedId ? t("security.common.save") : t("security.common.create")}
                </Button>
                {selectedId ? (
                  <Button type="button" variant="secondary" onClick={startCreate}>
                    {t("security.common.cancel")}
                  </Button>
                ) : null}
                <FormStatus tone="danger" message={formError} className="w-full" />
              </div>
            </form>
          </CardContent>
        </Card> : null}
      </main>
    </>
  );
}
