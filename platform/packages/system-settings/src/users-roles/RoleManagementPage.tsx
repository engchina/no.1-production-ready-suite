import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
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
  DataTable,
  type DataTableColumn,
  type DataTableSort,
  Button,
  StatusBadge,
  PageHeader,
  FieldError,
  PageBody,
  useConfirm,
  ProcessingIndicator,
  ObjectActionBar,
  cn,
  type EntityAction,
} from "@engchina/production-ready-ui";

import { useUnsavedChangesGuard } from "../guards/useUnsavedChangesGuard";
import { FieldLabel } from "../oci/required-field";
import { useRequestScope } from "../oci/useRequestScope";
import { FormActionBar, entityActionToFormAction } from "./FormActionBar";
import { t } from "./messages";
import {
  SECURITY_TABLE_ROW_CLASS,
  SECURITY_TABLE_VISIBLE_ROWS,
  SecurityDetailField,
  SecurityEmptySelection,
  SecurityIdentityLines,
  SecurityManagementPanelShell,
  SecurityPanelHeader,
  SecuritySearchField,
  describeErrorMessageOnly,
  identitySecondaryName,
  isAbortError,
  mapFieldErrors,
  securityFilteredCount,
  selectedVisibleKey,
  unmappedErrorMessage,
  useValuesChanged,
  withoutFieldError,
} from "./shared";
import {
  SYSTEM_ADMIN_ROLE_CODE,
  type DescribeApiError,
  type RoleManagementApi,
  type SecurityRole,
} from "./types";

type RolePanelView = "list" | "create" | "edit";

interface RoleDraftState {
  roleCode: string;
  displayName: string;
  description: string;
}

type RoleFormField = "roleCode" | "displayName";
type RoleFieldErrors = Partial<Record<RoleFormField, string>>;

const ROLE_POINTER_TO_FIELD = {
  "/role_code": "roleCode",
  "/display_name": "displayName",
} as const satisfies Readonly<Record<string, RoleFormField>>;

const EMPTY_DRAFT: RoleDraftState = { roleCode: "", displayName: "", description: "" };

const INPUT_CLASS =
  "h-11 w-full rounded-md border border-border bg-surface px-3 text-sm outline-none focus:border-focus-ring focus:ring-2 focus:ring-focus-ring disabled:bg-surface-hover disabled:text-fg-disabled";

function compareText(left: string, right: string, direction: DataTableSort["direction"]) {
  const result = left.localeCompare(right, "ja");
  return direction === "asc" ? result : -result;
}

function roleStatusText(role: SecurityRole) {
  if (role.archived) return t("security.roles.archivedDisabled");
  if (role.is_built_in) return t("security.roles.builtIn");
  return t("security.roles.custom");
}

export interface RoleManagementPageProps<R extends SecurityRole = SecurityRole> {
  api: RoleManagementApi<R>;
  /** ロールの作成・編集・アーカイブ・削除を行えるか（製品の権限判定）。 */
  canManage: boolean;
  /** 製品の API エラーから入力項目のエラーとエラーコードを取り出す。 */
  describeError?: DescribeApiError;
  /** 一覧と詳細の分割比率を保存する localStorage key の前置き。 */
  splitStoragePrefix?: string;
  /**
   * 詳細パネルの末尾に製品固有の情報を足す（例: 付与済みの権限数と権限管理への導線）。
   * ロールに付ける権限は製品ごとに違うため、共通画面では扱わない。
   */
  renderRoleDetailExtra?: (role: R) => ReactNode;
}

/** ロール管理（3製品共通。ロールの基本情報と状態だけを扱う。#206）。 */
export function RoleManagementPage<R extends SecurityRole = SecurityRole>({
  api,
  canManage,
  describeError = describeErrorMessageOnly,
  splitStoragePrefix,
  renderRoleDetailExtra,
}: RoleManagementPageProps<R>) {
  const confirm = useConfirm();
  const [roles, setRoles] = useState<R[]>([]);
  // 選択中のロールと、利用者が自分で選んだか（manual）を 1 つの state で持つ（render で manual を読むため ref にしない）。
  const [selection, setSelection] = useState<{ id: string | null; manual: boolean }>({
    id: null,
    manual: false,
  });
  const selectedId = selection.id;
  const selectRole = (id: string | null, manual = true) => setSelection({ id, manual });
  const [editingId, setEditingId] = useState<string | null>(null);
  const [activeView, setActiveView] = useState<RolePanelView>("list");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<DataTableSort>({ key: "role", direction: "asc" });
  const [draft, setDraft] = useState<RoleDraftState>(EMPTY_DRAFT);
  const [baseline, setBaseline] = useState<RoleDraftState>(EMPTY_DRAFT);
  const [changingRoleId, setChangingRoleId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [deletingRoleId, setDeletingRoleId] = useState<string | null>(null);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [formError, setFormError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<RoleFieldErrors>({});
  const formRef = useRef<HTMLFormElement | null>(null);
  const roleCodeRef = useRef<HTMLInputElement | null>(null);
  const displayNameRef = useRef<HTMLInputElement | null>(null);
  const loadSequence = useRef(0);
  const { abortAll, run: runScopedRequest } = useRequestScope();

  const editingRole = roles.find((role) => role.role_id === editingId) ?? null;
  const readOnly = Boolean(!canManage || editingRole?.is_built_in || editingRole?.archived);
  const mutationBusy = busy || deletingRoleId !== null || changingRoleId !== null;
  const operationBusy = mutationBusy || loading;
  const inputReadOnly = readOnly || operationBusy;
  const isDirty =
    activeView !== "list" &&
    (draft.roleCode !== baseline.roleCode ||
      draft.displayName !== baseline.displayName ||
      draft.description !== baseline.description);
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
  useUnsavedChangesGuard(isDirty || mutationBusy, confirmLeave);

  const filteredRoles = useMemo(() => {
    const q = search.trim().toLowerCase();
    return roles
      .filter((role) =>
        q
          ? [role.role_code, role.display_name, role.description, roleStatusText(role)]
              .join(" ")
              .toLowerCase()
              .includes(q)
          : true
      )
      .sort((left, right) => {
        if (sort.key === "status") {
          return compareText(roleStatusText(left), roleStatusText(right), sort.direction);
        }
        // 1 列目はロールコードを主表示するため、コードで並べる。
        return compareText(left.role_code, right.role_code, sort.direction);
      });
  }, [roles, search, sort]);

  const visibleSelectedId =
    activeView === "list"
      ? selectedVisibleKey(filteredRoles, selectedId, (role) => role.role_id, {
          preserveSelected: selection.manual,
        })
      : selectedId;
  const selectedRole = roles.find((role) => role.role_id === visibleSelectedId) ?? null;

  const load = async (announce = false) => {
    if (mutationBusy) return;
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    setLoading(true);
    setLoadError("");
    setActionError("");
    await requestData(sequence, announce);
  };

  // 読込中・エラー表示の初期化は呼び出し側で行う（初回表示は初期 state が読込中）。
  const requestData = async (sequence: number, announce: boolean) => {
    try {
      await runScopedRequest(async (signal) => {
        const roleRows = await api.roles(true, { signal });
        if (signal.aborted || sequence !== loadSequence.current) return;
        setRoles(roleRows);
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

  // 初回ロードは mount 時だけ行う。最新の requestData を commit 時に ref へ入れて呼ぶ
  // （requestData は props の api を読むため、deps に入れると再描画のたびに読み直しになる）。
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
    setBaseline(EMPTY_DRAFT);
    setFormError("");
    setFieldErrors({});
  };

  const startEdit = (role: R) => {
    selectRole(role.role_id);
    setEditingId(role.role_id);
    setActiveView("edit");
    const nextDraft = {
      roleCode: role.role_code,
      displayName: role.display_name,
      description: role.description,
    };
    setDraft(nextDraft);
    setBaseline(nextDraft);
    setFormError("");
    setFieldErrors({});
  };

  const finishToList = () => {
    setDraft(EMPTY_DRAFT);
    setBaseline(EMPTY_DRAFT);
    setActiveView("list");
    setEditingId(null);
    setFormError("");
    setFieldErrors({});
  };

  const returnToList = async () => {
    if (await confirmLeave()) finishToList();
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (inputReadOnly) return;
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
        const updated = await api.updateRole({
          ...editingRole,
          display_name: draft.displayName,
          description: draft.description,
        });
        setRoles((rows) => rows.map((row) => (row.role_id === updated.role_id ? updated : row)));
        startEdit(updated);
      } else {
        const created = await api.createRole({
          role_code: draft.roleCode,
          display_name: draft.displayName,
          description: draft.description,
        });
        setRoles((rows) => [...rows, created]);
        startEdit(created);
      }
      toast.success(t("security.common.saved"));
    } catch (cause) {
      const details = describeError(cause);
      const nextErrors = mapFieldErrors(
        details,
        ROLE_POINTER_TO_FIELD,
        (problem, problemDetails) =>
          problemDetails.code === "SECURITY_ROLE_CODE_CONFLICT" && problem.pointer === "/role_code"
            ? t("security.roles.codeConflict")
            : problemDetails.code === "SECURITY_ROLE_CODE_RESERVED" &&
                problem.pointer === "/role_code"
              ? t("security.roles.codeReserved")
              : problem.message
      );
      setFieldErrors(nextErrors);
      setFormError(
        unmappedErrorMessage(cause, details, ROLE_POINTER_TO_FIELD, t("security.common.saveError"))
      );
      if (Object.keys(nextErrors).length > 0) focusFirstFieldError(nextErrors);
    } finally {
      setBusy(false);
    }
  };

  const changeArchived = async (role: R, archive: boolean) => {
    if (!(await confirmLeave())) return;
    if (
      !(await confirm(
        archive
          ? {
              title: t("security.roles.archive"),
              description: t("security.roles.archiveConfirm"),
              tone: "danger",
            }
          : {
              title: t("security.roles.restore"),
              description: t("security.roles.restoreConfirm"),
              tone: "warning",
            }
      ))
    ) {
      return;
    }
    setChangingRoleId(role.role_id);
    setActionError("");
    try {
      const changed = archive ? await api.archiveRole(role) : await api.restoreRole(role);
      setRoles((rows) => rows.map((row) => (row.role_id === changed.role_id ? changed : row)));
      selectRole(changed.role_id);
      finishToList();
      toast.success(t("security.common.saved"));
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : t("security.common.saveError"));
    } finally {
      setChangingRoleId(null);
    }
  };

  const canDeleteRole = (role: R) => !role.is_built_in && role.archived;

  const handleDelete = async (role: R) => {
    if (operationBusy || !canDeleteRole(role)) return;
    selectRole(role.role_id);
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
      await api.deleteRole(role);
      const deletedIndex = filteredRoles.findIndex((row) => row.role_id === role.role_id);
      const nextRole = filteredRoles[deletedIndex + 1] ?? filteredRoles[deletedIndex - 1] ?? null;
      setRoles((rows) => rows.filter((row) => row.role_id !== role.role_id));
      selectRole(nextRole?.role_id ?? null, Boolean(nextRole));
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

  const roleActions = (role: R): EntityAction[] =>
    canManage
      ? [
          {
            id: "edit",
            label: t("security.common.edit"),
            icon: Pencil,
            disabled: operationBusy,
            onSelect: () => {
              if (!operationBusy) startEdit(role);
            },
          },
          {
            id: "archive",
            label: t("security.roles.archive"),
            icon: Archive,
            tone: "danger",
            visible: !role.is_built_in && !role.archived,
            disabled: operationBusy,
            loading: changingRoleId === role.role_id,
            onSelect: () => changeArchived(role, true),
          },
          {
            id: "restore",
            label: t("security.roles.restore"),
            icon: ArchiveRestore,
            visible: !role.is_built_in && role.archived,
            disabled: operationBusy,
            loading: changingRoleId === role.role_id,
            onSelect: () => changeArchived(role, false),
          },
          {
            id: "delete",
            label: t("security.roles.delete"),
            icon: Trash2,
            tone: "danger",
            visible: canDeleteRole(role),
            loading: deletingRoleId === role.role_id,
            disabled: operationBusy,
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

  const roleColumns: Array<DataTableColumn<R>> = [
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
      sortable: true,
      className: "min-w-32 align-top",
      render: (role) => <RoleStatusBadges role={role} />,
    },
    {
      key: "description",
      header: t("security.roles.description"),
      className: "min-w-40 align-top",
      render: (role) => (
        <span className="line-clamp-2 break-words text-fg-muted">
          {role.description || t("security.common.none")}
        </span>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        wide
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
                        disabled: operationBusy,
                        onClick: startCreate,
                      },
                    ]
                  : []),
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
        actionsLabel={t("security.roles.actionsLabel")}
        actionsTestId="security-roles-actions"
      />
      <PageBody wide className="grid gap-4">
        {loadError ? <Banner severity="danger">{loadError}</Banner> : null}
        {actionError ? <Banner severity="danger">{actionError}</Banner> : null}

        {activeView === "list" ? (
          <SecurityManagementPanelShell
            id="security-roles-panel-list"
            idPrefix="security-roles"
            ariaLabel={t("security.roles.workspaceLabel")}
            splitId="security-roles-list"
            splitStoragePrefix={splitStoragePrefix}
            preferredWidePane="right"
          >
            <section
              className="grid min-w-0 content-start gap-3"
              aria-labelledby="security-roles-list-heading"
            >
              <SecurityPanelHeader
                headingId="security-roles-list-heading"
                icon={Shield}
                title={t("security.roles.list")}
                description={t("security.roles.listHint")}
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
                  placeholder={t("security.roles.searchPlaceholder")}
                  value={search}
                  testId="security-roles-search"
                  disabled={operationBusy}
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
                testId="security-roles-grid"
                scrollAriaLabel={t("security.common.listScrollLabel", {
                  list: t("security.roles.list"),
                })}
                scrollTestId="security-roles-scroll-region"
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

            <RoleDetailPanel
              role={selectedRole}
              canManage={canManage}
              actions={selectedRole ? roleActions(selectedRole) : []}
              extra={selectedRole && renderRoleDetailExtra ? renderRoleDetailExtra(selectedRole) : null}
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
              id={`security-roles-panel-${activeView}`}
              idPrefix="security-roles"
              ariaLabel={t("security.roles.taskPanelLabel")}
            >
              <SecurityPanelHeader
                icon={activeView === "create" ? Plus : Pencil}
                title={
                  activeView === "edit"
                    ? t("security.roles.form.edit")
                    : t("security.roles.form.create")
                }
                description={t("security.roles.formHint")}
                headingId="security-roles-form-heading"
              />
              <form
                ref={formRef}
                className="grid gap-6"
                onSubmit={handleSubmit}
                aria-labelledby="security-roles-form-heading"
              >
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
                      disabled={activeView === "edit" || inputReadOnly}
                      className={cn(INPUT_CLASS, fieldErrors.roleCode && "border-danger-fg")}
                      aria-invalid={fieldErrors.roleCode ? "true" : undefined}
                      aria-describedby={fieldErrors.roleCode ? "security-role-code-error" : undefined}
                      value={draft.roleCode}
                      onChange={(event) => {
                        if (inputReadOnly) return;
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
                      disabled={inputReadOnly}
                      className={cn(INPUT_CLASS, fieldErrors.displayName && "border-danger-fg")}
                      aria-invalid={fieldErrors.displayName ? "true" : undefined}
                      aria-describedby={
                        fieldErrors.displayName ? "security-role-name-error" : undefined
                      }
                      value={draft.displayName}
                      onChange={(event) => {
                        if (inputReadOnly) return;
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
                    disabled={inputReadOnly}
                    className="min-h-24 w-full rounded-md border border-border-control bg-surface px-3 py-2 text-sm outline-none focus:border-focus-ring focus:ring-2 focus:ring-focus-ring disabled:cursor-not-allowed disabled:bg-surface-hover disabled:text-fg-disabled"
                    value={draft.description}
                    onChange={(event) => {
                      if (inputReadOnly) return;
                      setDraft((current) => ({ ...current, description: event.target.value }));
                    }}
                  />
                </label>

                <FormActionBar
                  ariaLabel={t("security.roles.editActions")}
                  primaryActions={
                    !readOnly
                      ? [
                          {
                            id: "save",
                            label:
                              activeView === "edit"
                                ? t("security.common.save")
                                : t("security.common.create"),
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
                    ...formRoleActions("restore"),
                    {
                      id: "cancel",
                      label: t("security.common.cancel"),
                      disabled: operationBusy,
                      onClick: returnToList,
                    },
                  ]}
                  dangerActions={editingRole ? formRoleActions("archive", "delete") : []}
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
      </PageBody>
    </>
  );
}

export function RoleStatusBadges({ role }: { role: SecurityRole }) {
  return (
    <div className="flex flex-wrap gap-1">
      <StatusBadge
        icon={false}
        variant={role.is_built_in ? "info" : "neutral"}
        label={role.is_built_in ? t("security.roles.builtIn") : t("security.roles.custom")}
      />
      {role.archived ? (
        <StatusBadge variant="neutral" label={t("security.roles.archivedDisabled")} />
      ) : null}
    </div>
  );
}

function RoleDetailPanel({
  role,
  canManage,
  actions,
  extra,
}: {
  role: SecurityRole | null;
  canManage: boolean;
  actions: EntityAction[];
  extra: ReactNode;
}) {
  if (!role) {
    return (
      <SecurityEmptySelection
        title={t("security.roles.noSelectionTitle")}
        hint={t("security.roles.noSelectionHint")}
      />
    );
  }

  return (
    <section
      className="grid min-w-0 content-start gap-4 rounded-md border border-border bg-surface-sunken p-4"
      aria-labelledby="security-roles-detail-heading"
    >
      <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2
              id="security-roles-detail-heading"
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
        {canManage ? (
          <ObjectActionBar
            actions={actions}
            ariaLabel={`${t("security.common.actions")}: ${role.role_code}`}
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
        <SecurityDetailField label={t("security.common.version")}>
          {String(role.version)}
        </SecurityDetailField>
        <SecurityDetailField label={t("security.roles.description")}>
          {role.description || t("security.common.none")}
        </SecurityDetailField>
      </dl>
      {extra}
    </section>
  );
}
