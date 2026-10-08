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
  LockKeyhole,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  Shield,
  ShieldCheck,
  Trash2,
} from "lucide-react";

import { Link } from "react-router-dom";

import {
  Banner,
  ButtonLink,
  EmptyState,
  ErrorState,
  toast,
  DataTable,
  type DataTableColumn,
  type DataTableSort,
  StatusBadge,
  PageHeader,
  PageBody,
  useConfirm,
  ProcessingIndicator,
  ObjectActionBar,
  type EntityAction,
  SaveErrorBanner,
  INFORMATION_TABLE_ROW_CLASS,
  INFORMATION_TABLE_VISIBLE_ROWS,
  TextareaField,
  TextField,
} from "@production-ready/ui";

import { useUnsavedChangesGuard } from "../guards/useUnsavedChangesGuard";
import { useRequestScope } from "../oci/useRequestScope";
import { t } from "./messages";
import {
  SecurityDetailField,
  SecurityEmptySelection,
  SecurityManagementPanelShell,
  SecurityPanelHeader,
  SecuritySearchField,
  SecurityClearSearchAction,
  describeErrorMessageOnly,
  SecurityIdentityRowTitleButton,
  identitySecondaryName,
  isAbortError,
  mapFieldErrors,
  securityFilteredCount,
  selectedVisibleKey,
  unmappedErrorMessage,
  useFocusAfterCommit,
  useValuesChanged,
  withoutFieldError,
} from "./shared";
import {
  SYSTEM_ADMIN_ROLE_CODE,
  type DescribeApiError,
  type RoleManagementApi,
  type SecurityRole,
} from "./types";
import { roleCodeValidationError } from "./validation";

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
   * 詳細パネルの末尾に、ロールに付けた機能権限の件数と、製品の権限管理への導線を出す（3 製品で同じ形。#800）。
   * ロールに付ける権限は製品ごとの権限管理が扱うため、ここでは件数と移動先だけを受け取る。
   */
  permissionSummary?: RolePermissionSummary<R>;
}

/** ロールの詳細に出す、機能権限の件数と権限管理への導線（#800）。 */
export interface RolePermissionSummary<R extends SecurityRole = SecurityRole> {
  /** ロールに付けている機能権限の数（製品の API のロールが持つ権限の一覧の件数）。 */
  count: (role: R) => number;
  /** 製品の権限管理の画面のパス。`?role=<role_id>` を付けて開く。 */
  permissionsPath: string;
  /** 権限管理を開けるか（製品の権限判定）。false なら件数だけを出す。 */
  canManagePermissions: boolean;
}

/** ロール管理（3製品共通。ロールの基本情報と状態だけを扱う。#206）。 */
export function RoleManagementPage<R extends SecurityRole = SecurityRole>({
  api,
  canManage,
  describeError = describeErrorMessageOnly,
  splitStoragePrefix,
  permissionSummary,
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
  // 「表示を更新」を押した読み直しの間だけ、そのボタンを回す。初回の読込・エラーからの再試行は
  // 押していないので、一覧の読込表示がスピナーを出し、「表示を更新」は disabled だけにする（#819）。
  const [refreshRequested, setRefreshRequested] = useState(false);
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
  // 保存を試みた回数（保存の失敗の Banner を、同じ文言の失敗でも入れ直す。#585）。
  const [submitAttempt, setSubmitAttempt] = useState(0);
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
  // 初回の読み込みに失敗してロールが 1 件も無いときは、空の一覧ではなく失敗と再試行を出す（#1038）。
  // 表示を更新したときの失敗は、前の一覧を残して Banner で出す。
  const initialLoadFailed = Boolean(loadError) && roles.length === 0;

  const load = async (announce = false) => {
    if (mutationBusy) return;
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    setLoading(true);
    setRefreshRequested(announce);
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

  // 送信中は欄が disabled のため、操作できる状態を commit した後に移す（#424）。
  const scheduleFocus = useFocusAfterCommit(!inputReadOnly);
  const focusFirstFieldError = (errors: RoleFieldErrors) => {
    scheduleFocus(() => {
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
    setSubmitAttempt((current) => current + 1);
    const normalizedRoleCode = draft.roleCode.trim().toUpperCase();
    // 未入力は送信前に欄の下へ出し、最初のエラーの欄へフォーカスする（noValidate。#531）。
    const requiredErrors: RoleFieldErrors = {};
    // backend（RoleCreateRequest）と同じ規則: 2〜64 文字、英大文字で始まり英大文字・数字・アンダースコア（#540）。
    if (activeView === "create") {
      const roleCodeError = roleCodeValidationError(normalizedRoleCode);
      if (roleCodeError) requiredErrors.roleCode = t(roleCodeError);
    }
    if (!draft.displayName.trim()) {
      requiredErrors.displayName = t("security.roles.nameRequired");
    }
    if (Object.keys(requiredErrors).length > 0) {
      setFormError("");
      setFieldErrors(requiredErrors);
      focusFirstFieldError(requiredErrors);
      return;
    }
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
            // 押したボタンは loading（aria-disabled）でフォーカスを保つ。ネイティブの disabled にしない（#355 / #835）。
            disabled: operationBusy && !(changingRoleId === role.role_id),
            loading: changingRoleId === role.role_id,
            onSelect: () => changeArchived(role, true),
          },
          {
            id: "restore",
            label: t("security.roles.restore"),
            icon: ArchiveRestore,
            visible: !role.is_built_in && role.archived,
            // 押したボタンは loading（aria-disabled）でフォーカスを保つ。ネイティブの disabled にしない（#355 / #835）。
            disabled: operationBusy && !(changingRoleId === role.role_id),
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
            // 押したボタンは loading（aria-disabled）でフォーカスを保つ。ネイティブの disabled にしない（#355 / #835）。
            disabled: operationBusy && !(deletingRoleId === role.role_id),
            onSelect: () => handleDelete(role),
          },
        ]
      : [];

  /**
   * 編集フォームの対象への操作（復元・アーカイブ・削除）。フォームのパネルの見出しの右の ObjectActionBar
   * 1 か所に置く（#618）。保存はページの右上（PageHeader）の primary。
   */
  const editObjectActions = (): EntityAction[] => {
    if (!editingRole) return [];
    const ids = new Set(["restore", "archive", "delete"]);
    return roleActions(editingRole).filter((action) => ids.has(action.id));
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
          <SecurityIdentityRowTitleButton
            id={role.role_code}
            name={role.display_name}
            current={selected}
            aria-label={t("security.roles.showRole", { name: role.role_code })}
            onClick={(event) => {
              event.stopPropagation();
              if (operationBusy) return;
              selectRole(role.role_id);
            }}
          />
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
                ...(canManage && !initialLoadFailed
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
                  // 押したボタンは loading（aria-disabled）でフォーカスを保つ。ネイティブの disabled にしない（#355 / #835）。
                  disabled: operationBusy && !(loading && refreshRequested),
                  onClick: () => load(true),
                  loading: loading && refreshRequested,
                },
              ]
            : !readOnly
              ? [
                  {
                    id: activeView === "edit" ? "save" : "create",
                    kind: "primary" as const,
                    label: activeView === "edit" ? t("security.common.save") : t("security.common.create"),
                    icon: activeView === "edit" ? Save : Plus,
                    loading: busy,
                    // 押したボタンは loading（aria-disabled）でフォーカスを保つ。ネイティブの disabled にしない（#355 / #835）。
                    disabled: operationBusy && !busy,
                    testId: "security-roles-submit",
                    onClick: () => formRef.current?.requestSubmit(),
                  },
                ]
              : []
        }
        // 詳細・作成・編集の画面の「一覧へ戻る」は左上、保存・作成は右端の primary（#618）。
        back={
          activeView === "list"
            ? undefined
            : { label: t("security.common.backToList"), onClick: () => void returnToList(), disabled: operationBusy, testId: "security-roles-back" }
        }
        actionsLabel={t("security.roles.actionsLabel")}
        actionsTestId="security-roles-actions"
      />
      <PageBody wide className="grid gap-4">
        {loadError && !initialLoadFailed ? <Banner severity="danger">{loadError}</Banner> : null}
        {/* 編集の画面の操作の失敗は SaveErrorBanner の 1 か所に出す（二重に出さない。#1038）。 */}
        {activeView === "list" && actionError ? (
          <Banner severity="danger">{actionError}</Banner>
        ) : null}

        {initialLoadFailed ? (
          <ErrorState message={loadError} onRetry={() => void load()} />
        ) : activeView === "list" ? (
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
                  resultCountLabel={securityFilteredCount(filteredRoles.length, roles.length)}
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
                  activityIcon={refreshRequested ? "none" : "spinner"}
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
                  "aria-label": t("security.roles.showRole", { name: role.role_code }),
                })}
                ariaLabel={t("security.roles.list")}
                testId="security-roles-grid"
                scrollAriaLabel={t("security.common.listScrollLabel", {
                  list: t("security.roles.list"),
                })}
                scrollTestId="security-roles-scroll-region"
                stickyHeader
                visibleRows={INFORMATION_TABLE_VISIBLE_ROWS}
                empty={
                  <EmptyState
                    title={search ? t("security.roles.noResultsTitle") : t("security.common.empty")}
                    hint={search ? t("security.roles.noResultsHint") : undefined}
                    action={search ? <SecurityClearSearchAction onClear={() => setSearch("")} /> : undefined}
                  />
                }
                columns={roleColumns}
              />
            </section>

            <RoleDetailPanel
              role={selectedRole}
              canManage={canManage}
              actions={selectedRole ? roleActions(selectedRole) : []}
              extra={
                selectedRole && permissionSummary ? (
                  <RolePermissionSummaryRow role={selectedRole} summary={permissionSummary} />
                ) : null
              }
            />
          </SecurityManagementPanelShell>
        ) : (
          <>
            {/* 保存の失敗はヘッダーの直下の 1 か所だけ（messaging.md §3.3.1。#585）。 */}
            <SaveErrorBanner
              message={formError || (activeView === "edit" ? actionError : "")}
              attemptKey={submitAttempt}
              testId="security-roles-save-error"
            />
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
                action={
                  activeView === "edit" ? (
                    <ObjectActionBar
                      actions={editObjectActions()}
                      ariaLabel={t("security.roles.editActions")}
                      testId="security-roles-object-actions"
                    />
                  ) : undefined
                }
              />
              <form
                ref={formRef}
                className="grid gap-6"
                onSubmit={handleSubmit}
                aria-labelledby="security-roles-form-heading"
                noValidate
              >
                {editingRole?.role_code === SYSTEM_ADMIN_ROLE_CODE ? (
                  <Banner severity="info">{t("security.roles.systemAdminNotice")}</Banner>
                ) : null}
                <div className="grid gap-4 sm:grid-cols-2">
                  <TextField
                    ref={roleCodeRef}
                    id="security-role-code"
                    label={t("security.roles.code")}
                    required
                    disabled={activeView === "edit" || inputReadOnly}
                    error={fieldErrors.roleCode}
                    value={draft.roleCode}
                    onValueChange={(value) => {
                      if (inputReadOnly) return;
                      setDraft((current) => ({
                        ...current,
                        roleCode: value.toUpperCase(),
                      }));
                      clearFieldError("roleCode");
                    }}
                  />
                  <TextField
                    ref={displayNameRef}
                    id="security-role-name"
                    label={t("security.roles.name")}
                    required
                    disabled={inputReadOnly}
                    error={fieldErrors.displayName}
                    value={draft.displayName}
                    onValueChange={(value) => {
                      if (inputReadOnly) return;
                      setDraft((current) => ({ ...current, displayName: value }));
                      clearFieldError("displayName");
                    }}
                  />
                </div>
                <TextareaField
                  id="security-role-description"
                  label={t("security.roles.description")}
                  disabled={inputReadOnly}
                  value={draft.description}
                  onValueChange={(value) => {
                    if (inputReadOnly) return;
                    setDraft((current) => ({ ...current, description: value }));
                  }}
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

/**
 * 機能権限の件数と権限管理への導線（#800）。詳細の区切り線の下に置き、枠の中に枠を重ねない。
 * 組み込み・アーカイブ済みのロールには権限を付けられないため、導線を出さない。
 */
function RolePermissionSummaryRow<R extends SecurityRole>({
  role,
  summary,
}: {
  role: R;
  summary: RolePermissionSummary<R>;
}) {
  return (
    <div
      className="flex flex-col gap-3 border-t border-border pt-4 sm:flex-row sm:items-center sm:justify-between"
      data-testid="security-roles-permission-summary"
    >
      <p className="text-sm text-fg">
        {role.role_code === SYSTEM_ADMIN_ROLE_CODE
          ? t("security.roles.permissionSummarySystemAdmin")
          : t("security.roles.permissionSummary", { count: summary.count(role) })}
      </p>
      {summary.canManagePermissions && !role.is_built_in && !role.archived ? (
        <ButtonLink
          to={`${summary.permissionsPath}?role=${encodeURIComponent(role.role_id)}`}
          linkComponent={Link}
          size="sm"
          icon={LockKeyhole}
          testId="security-roles-open-permissions"
        >
          {t("security.roles.openPermissions")}
        </ButtonLink>
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
