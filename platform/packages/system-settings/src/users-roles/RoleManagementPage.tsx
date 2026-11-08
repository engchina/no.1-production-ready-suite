import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";

import {
  Archive,
  ArchiveRestore,
  LockKeyhole,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  ShieldCheck,
  Trash2,
} from "lucide-react";

import { Link } from "react-router-dom";

import {
  compareNames,
  Banner,
  ButtonLink,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  ErrorState,
  toast,
  DataTable,
  type DataTableColumn,
  type DataTableSort,
  StatusBadge,
  PageHeader,
  PageBody,
  FormGrid,
  ListToolbar,
  listCountText,
  useConfirm,
  ProcessingIndicator,
  ObjectActionBar,
  RowActionMenu,
  FormActionBar,
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
import { RoleNameRowTitleButton, roleDisplayName } from "./RoleNameBadges";
import {
  SECURITY_NEW_ENTITY_ID,
  SecurityEditorFallback,
  SecuritySearchField,
  SecurityClearSearchAction,
  SecurityListPagination,
  describeErrorMessageOnly,
  isAbortError,
  mapFieldErrors,
  securityListCount,
  unmappedErrorMessage,
  useFocusAfterCommit,
  usePagedSelectionList,
  useReturnFocusToRow,
  useSecurityEditorSelection,
  withoutFieldError,
} from "./shared";
import {
  SYSTEM_ADMIN_ROLE_CODE,
  type DescribeApiError,
  type RoleManagementApi,
  type SecurityRole,
} from "./types";
import { roleCodeValidationError } from "./validation";

/** 一覧（null）・新規作成・編集・URL の対象が無い（読み込み中を含む）。 */
type RoleView = "list" | "create" | "edit" | "missing";

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
  return compareNames(left, right, direction);
}

/** ロールの種類（組み込み / カスタム / アーカイブ済み）。一覧の列・狭い画面の補足・検索に使う。 */
function roleKindText(role: SecurityRole) {
  if (role.archived) return t("security.roles.archived");
  if (role.is_built_in) return t("security.roles.builtIn");
  return t("security.roles.custom");
}

function draftFromRole(role: SecurityRole): RoleDraftState {
  return { roleCode: role.role_code, displayName: role.display_name, description: role.description };
}

export interface RoleManagementPageProps<R extends SecurityRole = SecurityRole> {
  api: RoleManagementApi<R>;
  /** ロールの作成・編集・アーカイブ・削除を行えるか（製品の権限判定）。false なら同じエディタを読み取り専用で出す。 */
  canManage: boolean;
  /** 製品の API エラーから入力項目のエラーとエラーコードを取り出す。 */
  describeError?: DescribeApiError;
  /**
   * エディタの基本情報の下に、ロールに付けた機能権限の件数と、製品の権限管理への導線を出す（3 製品で同じ形。#800）。
   * ロールに付ける権限は製品ごとの権限管理が扱うため、ここでは件数と移動先だけを受け取る。
   */
  permissionSummary?: RolePermissionSummary<R>;
}

/** ロールのエディタに出す、機能権限の件数と権限管理への導線（#800）。 */
export interface RolePermissionSummary<R extends SecurityRole = SecurityRole> {
  /** ロールに付けている機能権限の数（製品の API のロールが持つ権限の一覧の件数）。 */
  count: (role: R) => number;
  /** 製品の権限管理の画面のパス。`?role=<role_id>` を付けて開く。 */
  permissionsPath: string;
  /** 権限管理を開けるか（製品の権限判定）。false なら件数だけを出す。 */
  canManagePermissions: boolean;
}

/**
 * ロール管理（3製品共通。ロールの基本情報と状態だけを扱う。#206）。全幅の一覧 → 全幅のエディタ（A 型。#4635）。
 * 開いている対象は URL の `?role=`（`new` は新規作成）が唯一の情報源。
 */
export function RoleManagementPage<R extends SecurityRole = SecurityRole>({
  api,
  canManage,
  describeError = describeErrorMessageOnly,
  permissionSummary,
}: RoleManagementPageProps<R>) {
  const confirm = useConfirm();
  const [roles, setRoles] = useState<R[]>([]);
  const selection = useSecurityEditorSelection("role");
  const target = selection.selectedId;
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<DataTableSort>({ key: "role", direction: "asc" });
  const [draft, setDraft] = useState<RoleDraftState>(EMPTY_DRAFT);
  const [baseline, setBaseline] = useState<RoleDraftState>(EMPTY_DRAFT);
  // 今の draft がどの対象のものか（"new" / role_id / null）。URL の対象が変わったら入れ直す。
  const [draftFor, setDraftFor] = useState<string | null>(null);
  const [lastOpenedId, setLastOpenedId] = useState<string | null>(null);
  const [changingRoleId, setChangingRoleId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // 「最新情報を取得」を押した読み直しの間だけ、そのボタンを回す。初回の読込・エラーからの再試行は
  // 押していないので、一覧の読込表示がスピナーを出し、「最新情報を取得」は disabled だけにする（#819）。
  const [refreshRequested, setRefreshRequested] = useState(false);
  const initialLoading = loading && !refreshRequested;
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

  const creating = target === SECURITY_NEW_ENTITY_ID && canManage;
  const editingRole = target && !creating ? (roles.find((role) => role.role_id === target) ?? null) : null;
  const view: RoleView = !target ? "list" : creating ? "create" : editingRole ? "edit" : "missing";
  const draftKey = view === "create" ? SECURITY_NEW_ENTITY_ID : view === "edit" ? (editingRole?.role_id ?? null) : null;

  // URL の対象（新規作成・別のロール・一覧）が変わったら、フォームをその対象の値で入れ直す（render 中に同期する）。
  if (draftKey !== draftFor) {
    const nextDraft = editingRole && view === "edit" ? draftFromRole(editingRole) : EMPTY_DRAFT;
    setDraftFor(draftKey);
    setDraft(nextDraft);
    setBaseline(nextDraft);
    setFormError("");
    setFieldErrors({});
  }
  if (target && target !== SECURITY_NEW_ENTITY_ID && target !== lastOpenedId) {
    setLastOpenedId(target);
  }

  const readOnly = Boolean(!canManage || editingRole?.is_built_in || editingRole?.archived);
  const mutationBusy = busy || deletingRoleId !== null || changingRoleId !== null;
  const operationBusy = mutationBusy || loading;
  // 保存を試みた回数（保存の失敗の Banner を、同じ文言の失敗でも入れ直す。#585）。
  const [submitAttempt, setSubmitAttempt] = useState(0);
  const inputReadOnly = readOnly || operationBusy;
  const isDirty =
    (view === "create" || view === "edit") &&
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
  // 破棄して一覧へ戻るとき、離脱ガードが外れた commit の後で履歴を戻す（確認を二重に出さない）。
  const scheduleLeave = useFocusAfterCommit(!isDirty);

  const filteredRoles = useMemo(() => {
    const q = search.trim().toLowerCase();
    return roles
      .filter((role) =>
        q
          ? [role.role_code, role.display_name, role.description, roleKindText(role)]
              .join(" ")
              .toLowerCase()
              .includes(q)
          : true
      )
      .sort((left, right) => {
        if (sort.key === "kind") {
          return compareText(roleKindText(left), roleKindText(right), sort.direction);
        }
        if (sort.key === "code") {
          return compareText(left.role_code, right.role_code, sort.direction);
        }
        // 1 列目はロール名を出すため、ロール名で並べる（#1682）。
        return compareText(roleDisplayName(left), roleDisplayName(right), sort.direction);
      });
  }, [roles, search, sort]);

  // 一覧は 25 件ずつのページ（表示件数は選べる）。検索語・並べ替えが変わったら 1 ページ目へ戻す（#1891）。
  const roleList = usePagedSelectionList({
    items: filteredRoles,
    getKey: (role) => role.role_id,
    selectedId: lastOpenedId,
    active: view === "list",
    ready: !loading,
    resetKey: `${search}\u0000${sort.key}\u0000${sort.direction}`,
    pagingKey: "roles",
  });
  const roleListCount = securityListCount(roles.length, filteredRoles.length, search);
  useReturnFocusToRow(view === "list", lastOpenedId);
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

  const openRole = (role: R) => {
    if (operationBusy) return;
    setActionError("");
    selection.open(role.role_id);
  };

  const startCreate = () => {
    setActionError("");
    selection.open(SECURITY_NEW_ENTITY_ID);
  };

  /** 保存・作成の結果を基準にしてエディタに残る（作成のときは作った対象の URL へ置き換える）。 */
  const keepEditing = (role: R) => {
    const nextDraft = draftFromRole(role);
    setDraftFor(role.role_id);
    setDraft(nextDraft);
    setBaseline(nextDraft);
    setFormError("");
    setFieldErrors({});
    selection.replace(role.role_id);
  };

  const returnToList = async () => {
    if (!(await confirmLeave())) return;
    setActionError("");
    if (isDirty) {
      setDraft(baseline);
      scheduleLeave(selection.close);
      return;
    }
    selection.close();
  };

  // 変更を破棄: 入力を開いたときの値に戻す（#1786。変更が無いときは disabled）。
  const discardChanges = () => {
    setDraft(baseline);
    setFieldErrors({});
    setFormError("");
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (inputReadOnly) return;
    setSubmitAttempt((current) => current + 1);
    const normalizedRoleCode = draft.roleCode.trim().toUpperCase();
    // 未入力は送信前に欄の下へ出し、最初のエラーの欄へフォーカスする（noValidate。#531）。
    const requiredErrors: RoleFieldErrors = {};
    // backend（RoleCreateRequest）と同じ規則: 2〜64 文字、英大文字で始まり英大文字・数字・アンダースコア（#540）。
    if (view === "create") {
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
    if (view === "create" && normalizedRoleCode === SYSTEM_ADMIN_ROLE_CODE) {
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
      if (view === "edit") {
        if (!editingRole) return;
        const updated = await api.updateRole({
          ...editingRole,
          display_name: draft.displayName,
          description: draft.description,
        });
        setRoles((rows) => rows.map((row) => (row.role_id === updated.role_id ? updated : row)));
        keepEditing(updated);
      } else {
        const created = await api.createRole({
          role_code: draft.roleCode,
          display_name: draft.displayName,
          description: draft.description,
        });
        setRoles((rows) => [...rows, created]);
        keepEditing(created);
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
      // エディタで操作したときは、変わった状態を基準にして入力を入れ直す（未保存の入力は確認の上で捨てている）。
      if (target === changed.role_id) {
        const nextDraft = draftFromRole(changed);
        setDraft(nextDraft);
        setBaseline(nextDraft);
      }
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
    if (
      !(await confirm({
        title: t("security.roles.delete"),
        description: t("security.roles.deleteConfirm", { name: roleDisplayName(role) }),
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
      setRoles((rows) => rows.filter((row) => row.role_id !== role.role_id));
      // 削除した対象のエディタは閉じて一覧へ戻す（ほかの対象へ置き換えない）。
      if (target === role.role_id) selection.replace(null);
      setLastOpenedId(null);
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

  /** 対象への操作（行のメニューとエディタの ObjectActionBar で同じ定義。page-archetypes.md §0-6）。 */
  const stateActions = (role: R): EntityAction[] =>
    canManage
      ? [
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
            disabled: operationBusy && !(deletingRoleId === role.role_id),
            onSelect: () => handleDelete(role),
          },
        ]
      : [];

  // 全幅の表（#4635）。md 未満は先頭の列だけを残し、ロールコードと種類は名前の下の補足 1 行に畳む。
  const roleColumns: Array<DataTableColumn<R>> = [
    {
      key: "role",
      header: t("security.roles.column.role"),
      sortable: true,
      rowHeader: true,
      className: "align-top",
      render: (role) => (
        <RoleNameRowTitleButton
          securityRole={role}
          subtitle={
            <>
              {role.description ? <span className="line-clamp-1 break-all">{role.description}</span> : null}
              <span className="block md:hidden" data-testid="security-roles-row-summary">
                {`${role.role_code} · ${roleKindText(role)}`}
              </span>
            </>
          }
          href={selection.hrefFor(role.role_id)}
          data-security-row-id={role.role_id}
          aria-label={t("security.roles.showRole", { name: roleDisplayName(role) })}
          onClick={(event) => {
            event.stopPropagation();
            openRole(role);
          }}
        />
      ),
    },
    {
      key: "code",
      header: t("security.roles.code"),
      sortable: true,
      headerClassName: "hidden w-[16rem] md:table-cell",
      className: "hidden align-top md:table-cell",
      render: (role) => <code className="break-all font-mono text-xs">{role.role_code}</code>,
    },
    {
      key: "kind",
      header: t("security.roles.column.kind"),
      sortable: true,
      headerClassName: "hidden w-[10rem] md:table-cell",
      className: "hidden align-top md:table-cell",
      render: (role) => <RoleListStatusBadge role={role} />,
    },
    // 行のメニューは実際の操作だけ（開くのは行と名前のリンク。page-archetypes.md §0-7）。管理の権限が無ければ列を出さない。
    ...(canManage
      ? [
          {
            key: "actions",
            header: <span className="sr-only">{t("security.common.actions")}</span>,
            align: "right",
            headerClassName: "w-[3.5rem]",
            className: "align-top",
            render: (role: R) => (
              <RowActionMenu
                actions={stateActions(role)}
                ariaLabel={`${t("security.common.actions")}: ${roleDisplayName(role)}`}
                testId={`security-roles-row-actions-${role.role_id}`}
              />
            ),
          } satisfies DataTableColumn<R>,
        ]
      : []),
  ];

  const back = {
    label: t("security.common.backToList"),
    onClick: () => void returnToList(),
    disabled: operationBusy,
    testId: "security-roles-back",
  };
  const errorBanners = (
    <>
      {loadError && !initialLoadFailed ? <Banner severity="danger">{loadError}</Banner> : null}
      {actionError ? <Banner severity="danger">{actionError}</Banner> : null}
    </>
  );

  if (view === "missing") {
    return (
      <>
        <PageHeader wide title={t("nav.securityRoles")} back={back} />
        <PageBody wide className="grid gap-4">
          {initialLoadFailed ? (
            <ErrorState message={loadError} onRetry={() => void load()} />
          ) : (
            <SecurityEditorFallback
              loading={loading}
              loadingLabel={t("security.roles.loading")}
              title={t("security.roles.notFoundTitle")}
              hint={t("security.roles.notFoundHint")}
              onBack={() => selection.close()}
              testId="security-roles-detail-not-found"
            />
          )}
        </PageBody>
      </>
    );
  }

  if (view === "create" || view === "edit") {
    return (
      <>
        <PageHeader
          wide
          title={view === "edit" && editingRole ? roleDisplayName(editingRole) : t("security.roles.form.create")}
          subtitle={view === "create" ? t("security.roles.formHint") : undefined}
          status={view === "edit" && editingRole ? <RoleStatusBadges role={editingRole} /> : undefined}
          // 一覧へ戻るは左上（#618）。確定（作成・保存）はフォームの末尾の FormActionBar（#1786）。
          back={back}
        />
        <PageBody wide className="grid grid-cols-1 gap-5">
          {errorBanners}
          {editingRole?.role_code === SYSTEM_ADMIN_ROLE_CODE ? (
            <Banner severity="info">{t("security.roles.systemAdminNotice")}</Banner>
          ) : editingRole?.archived ? (
            <Banner severity="warning">{t("security.roles.archivedPermissionNotice")}</Banner>
          ) : editingRole?.is_built_in ? (
            <Banner severity="info">{t("security.roles.builtInReadonly")}</Banner>
          ) : null}
          <Card data-testid="security-roles-editor">
            <CardHeader className="flex-row flex-wrap items-start justify-between gap-3">
              <CardTitle id="security-roles-form-heading" className="flex items-center gap-2">
                <ShieldCheck size={20} aria-hidden="true" />
                {t("security.roles.section.basic")}
              </CardTitle>
              {view === "edit" && editingRole && canManage ? (
                // 対象への操作は最初のカードの見出しの右の 1 か所（#618）。
                <ObjectActionBar
                  actions={stateActions(editingRole)}
                  ariaLabel={t("security.roles.editActions")}
                  testId="security-roles-object-actions"
                />
              ) : null}
            </CardHeader>
            <CardContent>
              <form
                ref={formRef}
                className="grid gap-6"
                onSubmit={handleSubmit}
                aria-labelledby="security-roles-form-heading"
                noValidate
              >
                {/* 欄の並びは FormGrid（最大 60rem・置き場所 42rem 以上で 2 列）。説明は全幅（#1891）。 */}
                <FormGrid>
                  <TextField
                    ref={roleCodeRef}
                    id="security-role-code"
                    label={t("security.roles.code")}
                    required={view === "create"}
                    width="md"
                    disabled={view === "edit" || inputReadOnly}
                    error={fieldErrors.roleCode}
                    value={draft.roleCode}
                    inputClassName="font-mono"
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
                    required={!readOnly}
                    width="md"
                    disabled={inputReadOnly}
                    error={fieldErrors.displayName}
                    value={draft.displayName}
                    onValueChange={(value) => {
                      if (inputReadOnly) return;
                      setDraft((current) => ({ ...current, displayName: value }));
                      clearFieldError("displayName");
                    }}
                  />
                  <TextareaField
                    id="security-role-description"
                    label={t("security.roles.description")}
                    width="full"
                    className="col-span-full"
                    disabled={inputReadOnly}
                    value={draft.description}
                    onValueChange={(value) => {
                      if (inputReadOnly) return;
                      setDraft((current) => ({ ...current, description: value }));
                    }}
                  />
                </FormGrid>
                {view === "edit" && editingRole && permissionSummary ? (
                  <RolePermissionSummaryRow role={editingRole} summary={permissionSummary} />
                ) : null}
                {/* 保存の失敗は確定の行の直上の 1 か所だけ（messaging.md §3.3.1。#585 / #1786）。 */}
                <SaveErrorBanner
                  message={formError}
                  attemptKey={submitAttempt}
                  testId="security-roles-save-error"
                />
                {!readOnly ? (
                  // 確定はフォームの末尾の左（作成・保存 → 変更を破棄）。画面の下端に貼り付けない（#1786 / #2715）。
                  <FormActionBar
                    ariaLabel={t("security.roles.formActionsLabel")}
                    testId="security-roles-form-actions"
                    primaryActions={[
                      {
                        id: view === "edit" ? "save" : "create",
                        label: view === "edit" ? t("security.common.save") : t("security.common.submitCreate"),
                        icon: view === "edit" ? Save : Plus,
                        type: "submit",
                        loading: busy,
                        // 押したボタンは loading（aria-disabled）でフォーカスを保つ。ネイティブの disabled にしない（#355 / #835）。
                        disabled: operationBusy && !busy,
                        testId: "security-roles-submit",
                      },
                    ]}
                    secondaryActions={[
                      {
                        id: "discard",
                        label: t("security.common.discardChanges"),
                        icon: RotateCcw,
                        disabled: !isDirty || operationBusy,
                        testId: "security-roles-discard",
                        onClick: discardChanges,
                      },
                    ]}
                  />
                ) : null}
              </form>
            </CardContent>
          </Card>
        </PageBody>
      </>
    );
  }

  return (
    <>
      <PageHeader
        wide
        title={t("nav.securityRoles")}
        subtitle={t("security.roles.subtitle")}
        actions={[
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
        ]}
        actionsLabel={t("security.roles.actionsLabel")}
        actionsTestId="security-roles-actions"
      />
      <PageBody wide className="grid grid-cols-1 gap-4">
        {errorBanners}
        {initialLoadFailed ? (
          <ErrorState message={loadError} onRetry={() => void load()} />
        ) : (
          <section className="grid min-w-0 gap-3" aria-label={t("security.roles.list")} data-testid="security-roles-list">
            {/* 件数はツールバーの右の ListCount 1 か所（#1891）。件数の読み上げは検索欄の resultCountLabel が行う。 */}
            <ListToolbar
              testId="security-roles-toolbar"
              search={
                <SecuritySearchField
                  label={t("security.common.search")}
                  placeholder={t("security.roles.searchPlaceholder")}
                  value={search}
                  testId="security-roles-search"
                  disabled={operationBusy}
                  resultCountLabel={listCountText(roleListCount)}
                  onChange={setSearch}
                />
              }
              count={{ ...roleListCount, live: false, testId: "security-roles-count" }}
            />
            {/* 再読み込みは前の行を残し、ボタンの loading だけで示す。経過時間と Skeleton は最初の読み込みだけ（#4649）。 */}
            {initialLoading ? (
              <ProcessingIndicator
                active
                label={t("security.roles.loading")}
                operationKey="security-roles-load"
                placement="panel"
                testId="security-roles-loading"
              />
            ) : null}
            <div className="grid gap-2">
              <DataTable
                loading={initialLoading}
                rows={roleList.pageItems}
                sort={sort}
                onSortChange={(next) => {
                  if (!operationBusy) setSort(next);
                }}
                // 行の操作以外の領域のクリックでエディタを開く（page-archetypes.md §0-7）。キーボードは名前のリンク。
                onRowClick={openRole}
                getRowKey={(role) => role.role_id}
                rowProps={(role) => ({
                  className: INFORMATION_TABLE_ROW_CLASS,
                  "data-testid": `security-roles-row-${role.role_id}`,
                })}
                ariaLabel={t("security.roles.list")}
                testId="security-roles-grid"
                scrollAriaLabel={t("security.common.listScrollLabel", { list: t("security.roles.list") })}
                scrollTestId="security-roles-scroll-region"
                tableClassName="w-full"
                stickyHeader
                visibleRows={INFORMATION_TABLE_VISIBLE_ROWS}
                growToViewport
                empty={
                  <EmptyState
                    title={search ? t("security.roles.noResultsTitle") : t("security.common.empty")}
                    hint={search ? t("security.roles.noResultsHint") : undefined}
                    action={search ? <SecurityClearSearchAction onClear={() => setSearch("")} /> : undefined}
                  />
                }
                columns={roleColumns}
              />
              <SecurityListPagination
                list={roleList}
                ariaLabel={t("security.roles.pagination")}
                testId="security-roles-pagination"
                disabled={operationBusy}
              />
            </div>
          </section>
        )}
      </PageBody>
    </>
  );
}

/**
 * ロールの種類（1 つのバッジ。アーカイブ済み > 組み込み > カスタム）。一覧の種類の列と、権限管理の一覧で使う。
 * 権限が無効なことはエディタの `RoleStatusBadges` と注意の Banner で示す。
 */
export function RoleListStatusBadge({ role }: { role: SecurityRole }) {
  if (role.archived) return <StatusBadge variant="neutral" label={t("security.roles.archived")} />;
  return (
    <StatusBadge
      icon={false}
      variant={role.is_built_in ? "info" : "neutral"}
      label={role.is_built_in ? t("security.roles.builtIn") : t("security.roles.custom")}
    />
  );
}

/** エディタの見出しのロールの状態（種類と、アーカイブ済みなら権限が無効なこと）。 */
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
 * 機能権限の件数と権限管理への導線（#800）。基本情報の欄の下の区切り線の下に置き、枠の中に枠を重ねない。
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
      className="flex flex-col gap-3 border-t border-border pt-5 sm:flex-row sm:items-center sm:justify-between"
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
