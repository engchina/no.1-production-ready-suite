import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Archive, Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";

import {
  Banner,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
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
import type { DataEntitlement, PermissionDefinition, SecurityRole } from "./types";

interface RoleDraftState {
  roleCode: string;
  displayName: string;
  description: string;
  permissions: string[];
  dataEntitlements: DataEntitlement[];
}

const EMPTY_DRAFT: RoleDraftState = {
  roleCode: "",
  displayName: "",
  description: "",
  permissions: [],
  dataEntitlements: [],
};

const INPUT_CLASS =
  "h-10 w-full rounded-md border border-border bg-card px-3 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-ring/30 disabled:bg-muted/20 disabled:text-muted";

export function SecurityRolesPage() {
  const confirm = useConfirm();
  const { hasPermission } = useAuth();
  const canManage = hasPermission("security.roles.manage");
  const [roles, setRoles] = useState<SecurityRole[]>([]);
  const [permissions, setPermissions] = useState<PermissionDefinition[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<RoleDraftState>(EMPTY_DRAFT);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [formError, setFormError] = useState("");
  const loadSequence = useRef(0);

  const selectedRole = roles.find((role) => role.role_id === selectedId) ?? null;
  const permissionGroups = useMemo(() => {
    const groups = new Map<string, PermissionDefinition[]>();
    for (const permission of permissions) {
      const values = groups.get(permission.group) ?? [];
      values.push(permission);
      groups.set(permission.group, values);
    }
    return [...groups.entries()];
  }, [permissions]);

  const load = async () => {
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    setLoading(true);
    setLoadError("");
    setActionError("");
    try {
      const [roleRows, permissionRows] = await Promise.all([
        securityApi.roles(true),
        securityApi.permissions(),
      ]);
      if (sequence === loadSequence.current) {
        setRoles(roleRows);
        setPermissions(permissionRows);
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
  };

  const startEdit = (role: SecurityRole) => {
    setSelectedId(role.role_id);
    setDraft({
      roleCode: role.role_code,
      displayName: role.display_name,
      description: role.description,
      permissions: role.permissions,
      dataEntitlements: role.data_entitlements.map((item) => ({ ...item })),
    });
    setFormError("");
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setFormError("");
    try {
      const entitlementPayload = draft.dataEntitlements.map(({ resource_code, scope_code, capability }) => ({
        resource_code,
        scope_code,
        capability,
      }));
      if (selectedRole) {
        const updated = await securityApi.updateRole({
          ...selectedRole,
          display_name: draft.displayName,
          description: draft.description,
          permissions: draft.permissions,
          data_entitlements: entitlementPayload,
        });
        setRoles((rows) => rows.map((row) => (row.role_id === updated.role_id ? updated : row)));
        startEdit(updated);
      } else {
        const created = await securityApi.createRole({
          role_code: draft.roleCode,
          display_name: draft.displayName,
          description: draft.description,
          permissions: draft.permissions,
          data_entitlements: entitlementPayload,
        });
        setRoles((rows) => [...rows, created]);
        startEdit(created);
      }
      toast.success(t("security.common.saved"));
    } catch (cause) {
      setFormError(cause instanceof Error ? cause.message : t("security.common.saveError"));
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
      startCreate();
      toast.success(t("security.common.saved"));
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : t("security.common.saveError"));
    }
  };

  const togglePermission = (code: string) => {
    setDraft((current) => ({
      ...current,
      permissions: current.permissions.includes(code)
        ? current.permissions.filter((value) => value !== code)
        : [...current.permissions, code],
    }));
  };

  const addEntitlement = () => {
    setDraft((current) => ({
      ...current,
      dataEntitlements: [
        ...current.dataEntitlements,
        { resource_code: "NL2SQL_DEEPSEC_PROBE", scope_code: "*", capability: "ROW_READ" },
      ],
    }));
  };

  const updateEntitlement = (index: number, field: keyof DataEntitlement, value: string) => {
    setDraft((current) => ({
      ...current,
      dataEntitlements: current.dataEntitlements.map((item, itemIndex) =>
        itemIndex === index ? { ...item, [field]: value } : item
      ),
    }));
  };

  const readOnly = Boolean(!canManage || selectedRole?.is_built_in || selectedRole?.archived);

  return (
    <>
      <PageHeader
        className="px-4 sm:px-8"
        title={t("nav.securityRoles")}
        subtitle={t("security.roles.subtitle")}
        actions={
          <Button variant="secondary" size="sm" onClick={() => void load()} disabled={loading}>
            <RefreshCw size={14} aria-hidden />
            {t("security.common.reload")}
          </Button>
        }
      />
      <main
        className={`grid gap-5 p-4 xl:p-8 ${
          canManage ? "xl:grid-cols-[20rem_minmax(0,1fr)]" : "grid-cols-1"
        }`}
      >
        {loadError ? (
          <Banner severity="danger" className={canManage ? "xl:col-span-2" : undefined}>
            {loadError}
          </Banner>
        ) : null}
        <section className="space-y-4" aria-labelledby="security-role-list-title">
          {actionError ? <Banner severity="danger">{actionError}</Banner> : null}
          <Card>
            <CardHeader className="flex-row items-center justify-between gap-3">
              <CardTitle id="security-role-list-title">{t("security.roles.list")}</CardTitle>
              {canManage ? (
                <Button size="sm" onClick={startCreate}>
                  <Plus size={14} aria-hidden />
                  {t("security.common.create")}
                </Button>
              ) : null}
            </CardHeader>
            <CardContent className="space-y-2">
              {loading ? (
                <p className="py-6 text-center text-sm text-muted">{t("security.common.loading")}</p>
              ) : roles.length === 0 ? (
                <EmptyState title={t("security.common.empty")} />
              ) : (
                roles.map((role) => (
                  <div
                    key={role.role_id}
                    className={`rounded-md border p-3 ${selectedId === role.role_id ? "border-primary bg-primary/5" : "border-border"}`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      {canManage ? (
                        <button type="button" className="min-w-0 flex-1 cursor-pointer text-left focus-visible:outline-2 focus-visible:outline-ring" onClick={() => startEdit(role)}>
                          <span className="block truncate text-sm font-medium">{role.display_name}</span>
                          <span className="block truncate font-mono text-[11px] text-muted">{role.role_code}</span>
                        </button>
                      ) : (
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm font-medium">{role.display_name}</span>
                          <span className="block truncate font-mono text-[11px] text-muted">{role.role_code}</span>
                        </span>
                      )}
                      {canManage ? (
                        <Button size="sm" variant="ghost" aria-label={`${role.display_name}: ${t("security.common.edit")}`} onClick={() => startEdit(role)}>
                          <Pencil size={14} aria-hidden />
                        </Button>
                      ) : null}
                    </div>
                    <div className="mt-2 flex flex-wrap gap-1">
                      {role.is_built_in ? <StatusBadge variant="info" label={t("security.roles.builtIn")} /> : null}
                      {role.archived ? <StatusBadge variant="neutral" label={t("security.roles.archived")} /> : null}
                    </div>
                  </div>
                ))
              )}
            </CardContent>
          </Card>
        </section>

        {canManage ? <Card className="h-fit min-w-0">
          <CardHeader>
            <CardTitle>
              {selectedRole ? t("security.roles.form.edit") : t("security.roles.form.create")}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <form className="space-y-6" onSubmit={handleSubmit}>
              {selectedRole?.role_code === "SYSTEM_ADMIN" ? (
                <Banner severity="info">{t("security.roles.systemAdminNotice")}</Banner>
              ) : null}
              <div className="grid gap-4 sm:grid-cols-2">
                <label className="space-y-1.5 text-sm font-medium">
                  <span>{t("security.roles.code")}</span>
                  <input
                    required
                    disabled={Boolean(selectedRole)}
                    className={INPUT_CLASS}
                    value={draft.roleCode}
                    onChange={(event) => setDraft((current) => ({ ...current, roleCode: event.target.value.toUpperCase() }))}
                  />
                </label>
                <label className="space-y-1.5 text-sm font-medium">
                  <span>{t("security.roles.name")}</span>
                  <input
                    required
                    disabled={readOnly}
                    className={INPUT_CLASS}
                    value={draft.displayName}
                    onChange={(event) => setDraft((current) => ({ ...current, displayName: event.target.value }))}
                  />
                </label>
              </div>
              <label className="block space-y-1.5 text-sm font-medium">
                <span>{t("security.roles.description")}</span>
                <textarea
                  disabled={readOnly}
                  className="min-h-20 w-full rounded-md border border-border bg-card px-3 py-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-ring/30 disabled:bg-muted/20 disabled:text-muted"
                  value={draft.description}
                  onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))}
                />
              </label>

              <fieldset className="space-y-3" disabled={readOnly}>
                <legend className="text-base font-semibold">{t("security.roles.permissions")}</legend>
                <p className="text-sm text-muted">{t("security.roles.permissionsHint")}</p>
                <div className="grid gap-3 lg:grid-cols-2">
                  {permissionGroups.map(([group, groupPermissions]) => (
                    <div key={group} className="rounded-md border border-border p-3">
                      <h3 className="mb-2 text-sm font-semibold">{group}</h3>
                      <div className="space-y-2">
                        {groupPermissions.map((permission) => (
                          <label key={permission.code} className="flex cursor-pointer items-start gap-2 text-sm">
                            <input
                              className="mt-0.5 h-4 w-4 accent-primary"
                              type="checkbox"
                              checked={draft.permissions.includes(permission.code)}
                              onChange={() => togglePermission(permission.code)}
                            />
                            <span className="min-w-0">
                              <span className="block font-medium">{permission.label}</span>
                              <span className="block text-xs leading-5 text-muted">{permission.description}</span>
                              <code className="block break-all text-[10px] text-muted">{permission.code}</code>
                            </span>
                          </label>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </fieldset>

              <fieldset className="space-y-3" disabled={readOnly}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="text-base font-semibold">{t("security.roles.dataEntitlements")}</h3>
                  <Button type="button" size="sm" variant="secondary" onClick={addEntitlement} disabled={readOnly}>
                    <Plus size={14} aria-hidden />
                    {t("security.roles.addEntitlement")}
                  </Button>
                </div>
                <div className="space-y-2">
                  {draft.dataEntitlements.length === 0 ? (
                    <p className="rounded-md border border-dashed border-border p-4 text-sm text-muted">{t("security.common.empty")}</p>
                  ) : (
                    draft.dataEntitlements.map((entitlement, index) => (
                      <div key={`${index}-${entitlement.entitlement_id ?? "new"}`} className="grid gap-2 rounded-md border border-border p-3 sm:grid-cols-[1fr_1fr_1fr_auto]">
                        <label className="space-y-1 text-xs font-medium">
                          <span>{t("security.roles.resource")}</span>
                          <input className={INPUT_CLASS} value={entitlement.resource_code} onChange={(event) => updateEntitlement(index, "resource_code", event.target.value.toUpperCase())} />
                        </label>
                        <label className="space-y-1 text-xs font-medium">
                          <span>{t("security.roles.scope")}</span>
                          <input className={INPUT_CLASS} value={entitlement.scope_code} onChange={(event) => updateEntitlement(index, "scope_code", event.target.value)} />
                        </label>
                        <label className="space-y-1 text-xs font-medium">
                          <span>{t("security.roles.capability")}</span>
                          <select className={INPUT_CLASS} value={entitlement.capability} onChange={(event) => updateEntitlement(index, "capability", event.target.value)}>
                            <option value="ROW_READ">ROW_READ</option>
                            <option value="SENSITIVE_READ">SENSITIVE_READ</option>
                            <option value="FULL">FULL</option>
                          </select>
                        </label>
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          className="self-end"
                          aria-label={t("security.roles.removeEntitlement")}
                          onClick={() => setDraft((current) => ({ ...current, dataEntitlements: current.dataEntitlements.filter((_, itemIndex) => itemIndex !== index) }))}
                        >
                          <Trash2 size={14} aria-hidden />
                        </Button>
                      </div>
                    ))
                  )}
                </div>
              </fieldset>

              <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
                {!readOnly ? (
                  <Button loading={busy} type="submit">
                    {selectedRole ? t("security.common.save") : t("security.common.create")}
                  </Button>
                ) : null}
                {selectedRole && !selectedRole.is_built_in && !selectedRole.archived ? (
                  <Button type="button" variant="danger" onClick={() => void handleArchive(selectedRole)}>
                    <Archive size={15} aria-hidden />
                    {t("security.roles.archive")}
                  </Button>
                ) : null}
                <Button type="button" variant="secondary" onClick={startCreate}>
                  {t("security.common.cancel")}
                </Button>
                <FormStatus tone="danger" message={formError} className="w-full" />
              </div>
            </form>
          </CardContent>
        </Card> : null}
      </main>
    </>
  );
}
