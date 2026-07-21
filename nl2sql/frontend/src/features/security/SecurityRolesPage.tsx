import { useEffect, useMemo, useRef, useState, type FormEvent, type MouseEvent } from "react";
import { Archive, ArrowLeft, Pencil, Plus, RefreshCw, Shield, ShieldCheck, Trash2 } from "lucide-react";

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
import type { DataEntitlement, PermissionDefinition, SecurityRole } from "./types";

type RolePanelView = "list" | "create" | "edit";

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
  if (role.archived) return t("security.roles.archived");
  if (role.is_built_in) return t("security.roles.builtIn");
  return t("security.roles.custom");
}

export function SecurityRolesPage() {
  const confirm = useConfirm();
  const { hasPermission } = useAuth();
  const canManage = hasPermission("security.roles.manage");
  const [roles, setRoles] = useState<SecurityRole[]>([]);
  const [permissions, setPermissions] = useState<PermissionDefinition[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [activeView, setActiveView] = useState<RolePanelView>("list");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<DataTableSort>({ key: "role", direction: "asc" });
  const [draft, setDraft] = useState<RoleDraftState>(EMPTY_DRAFT);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [formError, setFormError] = useState("");
  const loadSequence = useRef(0);
  const { abortAll, run: runScopedRequest } = useRequestScope();

  const selectedRole = roles.find((role) => role.role_id === selectedId) ?? null;
  const editingRole = roles.find((role) => role.role_id === editingId) ?? null;
  const builtinRoles = roles.filter((role) => role.is_built_in).length;
  const archivedRoles = roles.filter((role) => role.archived).length;

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

  const rolePermissionText = (role: SecurityRole) =>
    role.permissions
      .map((code) => permissionByCode.get(code)?.label ?? code)
      .join(" ");

  const roleSearchText = (role: SecurityRole) =>
    [
      role.role_code,
      role.display_name,
      role.description,
      roleStatusText(role),
      rolePermissionText(role),
      ...role.data_entitlements.flatMap((item) => [item.resource_code, item.scope_code, item.capability]),
    ]
      .join(" ")
      .toLowerCase();

  const filteredRoles = useMemo(() => {
    const q = search.trim().toLowerCase();
    return roles
      .filter((role) => (q ? roleSearchText(role).includes(q) : true))
      .sort((left, right) => {
        if (sort.key === "status") return compareText(roleStatusText(left), roleStatusText(right), sort.direction);
        if (sort.key === "permissions") return compareNumber(left.permissions.length, right.permissions.length, sort.direction);
        if (sort.key === "dataEntitlements") return compareNumber(left.data_entitlements.length, right.data_entitlements.length, sort.direction);
        return compareText(left.display_name, right.display_name, sort.direction);
      });
  }, [permissionByCode, roles, search, sort]);

  const load = async () => {
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    setLoading(true);
    setLoadError("");
    setActionError("");
    try {
      await runScopedRequest(async (signal) => {
        const [roleRows, permissionRows] = await Promise.all([
          securityApi.roles(true, { signal }),
          securityApi.permissions({ signal }),
        ]);
        if (signal.aborted || sequence !== loadSequence.current) return;
        setRoles(roleRows);
        setPermissions(permissionRows);
        setSelectedId((current) =>
          current && roleRows.some((role) => role.role_id === current)
            ? current
            : roleRows[0]?.role_id ?? null
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
  };

  const startEdit = (role: SecurityRole) => {
    setSelectedId(role.role_id);
    setEditingId(role.role_id);
    setActiveView("edit");
    setDraft({
      roleCode: role.role_code,
      displayName: role.display_name,
      description: role.description,
      permissions: role.permissions,
      dataEntitlements: role.data_entitlements.map((item) => ({ ...item })),
    });
    setFormError("");
  };

  const returnToList = () => {
    setActiveView("list");
    setEditingId(null);
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
      if (activeView === "edit") {
        if (!editingRole) return;
        const updated = await securityApi.updateRole({
          ...editingRole,
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
      setSelectedId(archived.role_id);
      returnToList();
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

  const readOnly = Boolean(!canManage || editingRole?.is_built_in || editingRole?.archived);

  const roleColumns: Array<DataTableColumn<SecurityRole>> = [
    {
      key: "role",
      header: t("security.roles.column.role"),
      sortable: true,
      className: "min-w-52",
      render: (role) => {
        const selected = selectedId === role.role_id;
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
      render: (role) => t("security.roles.permissionCount", { count: role.permissions.length }),
    },
    {
      key: "dataEntitlements",
      header: t("security.roles.dataEntitlements"),
      sortable: true,
      className: "min-w-36",
      render: (role) => t("security.roles.entitlementCount", { count: role.data_entitlements.length }),
    },
    ...(canManage
      ? [
          {
            key: "actions",
            header: t("security.common.actions"),
            className: "min-w-40",
            render: (role: SecurityRole) => (
              <div className="flex flex-wrap justify-end gap-1">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={(event: MouseEvent<HTMLButtonElement>) => {
                    event.stopPropagation();
                    startEdit(role);
                  }}
                >
                  <Pencil size={14} aria-hidden />
                  {t("security.common.edit")}
                </Button>
                {!role.is_built_in && !role.archived ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={(event: MouseEvent<HTMLButtonElement>) => {
                      event.stopPropagation();
                      void handleArchive(role);
                    }}
                  >
                    <Archive size={14} aria-hidden />
                    {t("security.roles.archive")}
                  </Button>
                ) : null}
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
        title={t("nav.securityRoles")}
        subtitle={t("security.roles.subtitle")}
      />
      <main className="grid gap-4 p-4 lg:p-8">
        {loadError ? <Banner severity="danger">{loadError}</Banner> : null}
        {actionError ? <Banner severity="danger">{actionError}</Banner> : null}

        <SecurityManagementStatusBar
          ariaLabel={t("security.roles.statusBar")}
          metrics={[
            { label: t("security.roles.metric.total"), value: String(roles.length), emphasis: true, testId: "security-roles-metric-total" },
            { label: t("security.roles.metric.builtIn"), value: String(builtinRoles), testId: "security-roles-metric-built-in" },
            { label: t("security.roles.metric.archived"), value: String(archivedRoles), testId: "security-roles-metric-archived" },
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
                data-testid="security-roles-actions"
                aria-label={t("security.roles.actionsLabel")}
              >
                <Button type="button" size="sm" onClick={startCreate}>
                  <Plus size={15} aria-hidden="true" />
                  <span>{t("security.common.create")}</span>
                </Button>
              </div>
            ) : null}
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
                <DataTable
                  dense
                  loading={loading}
                  rows={filteredRoles}
                  sort={sort}
                  onSortChange={setSort}
                  onRowClick={(role) => setSelectedId(role.role_id)}
                  getRowKey={(role) => role.role_id}
                  ariaLabel={t("security.roles.list")}
                  testId="security-roles-grid"
                  empty={<EmptyState title={search ? t("security.roles.noResultsTitle") : t("security.common.empty")} hint={search ? t("security.roles.noResultsHint") : undefined} />}
                  columns={roleColumns}
                />
              </section>

              <RoleDetailPanel
                role={selectedRole}
                canManage={canManage}
                permissions={permissions}
                permissionByCode={permissionByCode}
                onEdit={startEdit}
                onArchive={(role) => void handleArchive(role)}
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
              <form className="grid gap-6" onSubmit={handleSubmit} aria-labelledby="security-roles-form-heading">
                {editingRole?.role_code === "SYSTEM_ADMIN" ? (
                  <Banner severity="info">{t("security.roles.systemAdminNotice")}</Banner>
                ) : null}
                <div className="grid gap-4 sm:grid-cols-2">
                  <label className="grid gap-1.5 text-sm font-medium">
                    <span>{t("security.roles.code")}</span>
                    <input
                      required
                      disabled={activeView === "edit"}
                      className={INPUT_CLASS}
                      value={draft.roleCode}
                      onChange={(event) => setDraft((current) => ({ ...current, roleCode: event.target.value.toUpperCase() }))}
                    />
                  </label>
                  <label className="grid gap-1.5 text-sm font-medium">
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
                <label className="grid gap-1.5 text-sm font-medium">
                  <span>{t("security.roles.description")}</span>
                  <textarea
                    disabled={readOnly}
                    className="min-h-24 w-full rounded-md border border-border bg-card px-3 py-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-ring/30 disabled:bg-muted/20 disabled:text-muted"
                    value={draft.description}
                    onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))}
                  />
                </label>

                <fieldset className="grid gap-3" disabled={readOnly}>
                  <legend className="text-base font-semibold">{t("security.roles.permissions")}</legend>
                  <p className="text-sm text-muted">{t("security.roles.permissionsHint")}</p>
                  {permissionGroups.length === 0 ? (
                    <p className="rounded-md border border-dashed border-border p-4 text-sm text-muted">{t("security.common.empty")}</p>
                  ) : (
                    <div className="grid gap-3 lg:grid-cols-2">
                      {permissionGroups.map(([group, groupPermissions]) => (
                        <div key={group} className="rounded-md border border-border p-3">
                          <h3 className="mb-2 text-sm font-semibold">{group}</h3>
                          <div className="grid gap-2">
                            {groupPermissions.map((permission) => (
                              <label key={permission.code} className="flex min-h-11 cursor-pointer items-start gap-2 text-sm">
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
                  )}
                </fieldset>

                <fieldset className="grid gap-3" disabled={readOnly}>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h3 className="text-base font-semibold">{t("security.roles.dataEntitlements")}</h3>
                    <Button type="button" size="sm" variant="secondary" onClick={addEntitlement} disabled={readOnly}>
                      <Plus size={14} aria-hidden />
                      {t("security.roles.addEntitlement")}
                    </Button>
                  </div>
                  {draft.dataEntitlements.length === 0 ? (
                    <p className="rounded-md border border-dashed border-border p-4 text-sm text-muted">{t("security.common.empty")}</p>
                  ) : (
                    <div className="grid gap-2">
                      {draft.dataEntitlements.map((entitlement, index) => (
                        <div
                          key={`${index}-${entitlement.entitlement_id ?? "new"}`}
                          className="grid gap-2 rounded-md border border-border p-3 sm:grid-cols-[1fr_1fr_1fr_auto]"
                        >
                          <label className="grid gap-1 text-xs font-medium">
                            <span>{t("security.roles.resource")}</span>
                            <input
                              className={INPUT_CLASS}
                              value={entitlement.resource_code}
                              onChange={(event) => updateEntitlement(index, "resource_code", event.target.value.toUpperCase())}
                            />
                          </label>
                          <label className="grid gap-1 text-xs font-medium">
                            <span>{t("security.roles.scope")}</span>
                            <input
                              className={INPUT_CLASS}
                              value={entitlement.scope_code}
                              onChange={(event) => updateEntitlement(index, "scope_code", event.target.value)}
                            />
                          </label>
                          <label className="grid gap-1 text-xs font-medium">
                            <span>{t("security.roles.capability")}</span>
                            <select
                              className={INPUT_CLASS}
                              value={entitlement.capability}
                              onChange={(event) => updateEntitlement(index, "capability", event.target.value)}
                            >
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
                            onClick={() =>
                              setDraft((current) => ({
                                ...current,
                                dataEntitlements: current.dataEntitlements.filter((_, itemIndex) => itemIndex !== index),
                              }))
                            }
                          >
                            <Trash2 size={14} aria-hidden />
                          </Button>
                        </div>
                      ))}
                    </div>
                  )}
                </fieldset>

                <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
                  {!readOnly ? (
                    <Button loading={busy} type="submit">
                      {activeView === "edit" ? t("security.common.save") : t("security.common.create")}
                    </Button>
                  ) : null}
                  {editingRole && !editingRole.is_built_in && !editingRole.archived ? (
                    <Button type="button" variant="danger" onClick={() => void handleArchive(editingRole)}>
                      <Archive size={15} aria-hidden />
                      {t("security.roles.archive")}
                    </Button>
                  ) : null}
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

function RoleStatusBadges({ role }: { role: SecurityRole }) {
  return (
    <div className="flex flex-wrap gap-1">
      <StatusBadge variant={role.is_built_in ? "info" : "neutral"} label={role.is_built_in ? t("security.roles.builtIn") : t("security.roles.custom")} />
      {role.archived ? <StatusBadge variant="neutral" label={t("security.roles.archived")} /> : null}
    </div>
  );
}

function RoleDetailPanel({
  role,
  canManage,
  permissions,
  permissionByCode,
  onEdit,
  onArchive,
}: {
  role: SecurityRole | null;
  canManage: boolean;
  permissions: PermissionDefinition[];
  permissionByCode: Map<string, PermissionDefinition>;
  onEdit: (role: SecurityRole) => void;
  onArchive: (role: SecurityRole) => void;
}) {
  if (!role) {
    return (
      <SecurityEmptySelection
        title={t("security.roles.noSelectionTitle")}
        hint={t("security.roles.noSelectionHint")}
      />
    );
  }

  const groupedPermissions = role.permissions.reduce<Array<{ group: string; items: string[] }>>((groups, code) => {
    const group = permissionByCode.get(code)?.group ?? t("security.roles.unknownGroup");
    const existing = groups.find((item) => item.group === group);
    if (existing) {
      existing.items.push(code);
    } else {
      groups.push({ group, items: [code] });
    }
    return groups;
  }, []);

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
          <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap xl:justify-end">
            <Button type="button" variant="secondary" size="sm" onClick={() => onEdit(role)}>
              <Pencil size={15} aria-hidden="true" />
              <span>{t("security.common.edit")}</span>
            </Button>
            {!role.is_built_in && !role.archived ? (
              <Button type="button" variant="danger" size="sm" onClick={() => onArchive(role)}>
                <Archive size={15} aria-hidden="true" />
                <span>{t("security.roles.archive")}</span>
              </Button>
            ) : null}
          </div>
        ) : null}
      </div>

      {role.role_code === "SYSTEM_ADMIN" ? (
        <Banner severity="info">{t("security.roles.systemAdminNotice")}</Banner>
      ) : null}

      <dl className="grid gap-3 md:grid-cols-2">
        <SecurityDetailField label={t("security.roles.code")}>
          <code className="break-all font-mono text-xs">{role.role_code}</code>
        </SecurityDetailField>
        <SecurityDetailField label={t("security.common.status")}>
          <RoleStatusBadges role={role} />
        </SecurityDetailField>
        <SecurityDetailField label={t("security.roles.permissions")}>
          {t("security.roles.permissionCount", { count: role.permissions.length })}
        </SecurityDetailField>
        <SecurityDetailField label={t("security.roles.dataEntitlements")}>
          {t("security.roles.entitlementCount", { count: role.data_entitlements.length })}
        </SecurityDetailField>
        <SecurityDetailField label={t("security.common.version")}>
          {String(role.version)}
        </SecurityDetailField>
        <SecurityDetailField label={t("security.roles.description")}>
          {role.description || t("security.common.none")}
        </SecurityDetailField>
      </dl>

      <section className="grid gap-2" aria-label={t("security.roles.permissions")}>
        <h3 className="text-sm font-semibold text-foreground">{t("security.roles.permissions")}</h3>
        {groupedPermissions.length === 0 ? (
          <p className="rounded-md border border-dashed border-border p-3 text-sm text-muted">
            {role.role_code === "SYSTEM_ADMIN" ? t("security.roles.systemAdminNotice") : t("security.common.none")}
          </p>
        ) : (
          <div className="grid gap-2">
            {groupedPermissions.map((group) => (
              <div key={group.group} className="rounded-md border border-border bg-card p-3">
                <h4 className="text-xs font-semibold text-muted">{group.group}</h4>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {group.items.map((code) => (
                    <StatusBadge
                      key={code}
                      variant="info"
                      label={permissionByCode.get(code)?.label ?? code}
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="grid gap-2" aria-label={t("security.roles.dataEntitlements")}>
        <h3 className="text-sm font-semibold text-foreground">{t("security.roles.dataEntitlements")}</h3>
        {role.data_entitlements.length === 0 ? (
          <p className="rounded-md border border-dashed border-border p-3 text-sm text-muted">{t("security.common.none")}</p>
        ) : (
          <div className="overflow-x-auto rounded-md border border-border bg-card">
            <table className="w-full min-w-[32rem] table-fixed divide-y divide-border text-left text-sm">
              <thead className="bg-background text-xs text-muted">
                <tr>
                  <th className="px-3 py-2 font-semibold">{t("security.roles.resource")}</th>
                  <th className="px-3 py-2 font-semibold">{t("security.roles.scope")}</th>
                  <th className="px-3 py-2 font-semibold">{t("security.roles.capability")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/70">
                {role.data_entitlements.map((entitlement, index) => (
                  <tr key={entitlement.entitlement_id ?? `${entitlement.resource_code}-${index}`}>
                    <td className="break-all px-3 py-2 font-mono text-xs">{entitlement.resource_code}</td>
                    <td className="break-all px-3 py-2 font-mono text-xs">{entitlement.scope_code}</td>
                    <td className="break-all px-3 py-2 font-mono text-xs">{entitlement.capability}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {permissions.length === 0 ? (
        <p className="rounded-md border border-warning/30 bg-warning-bg px-3 py-2 text-sm text-warning">
          {t("security.roles.permissionCatalogEmpty")}
        </p>
      ) : null}
    </section>
  );
}
