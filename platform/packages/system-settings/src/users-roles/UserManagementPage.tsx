import {
  compareNames,
  Banner,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CopyButton,
  EmptyState,
  ErrorState,
  FormStatus,
  toast,
  DataTable,
  type DataTableColumn,
  type DataTableSort,
  StatusBadge,
  PageHeader,
  FormGrid,
  ListToolbar,
  listCountText,
  FieldActionRow,
  FieldError,
  FieldLegend,
  TextField,
  PageBody,
  useConfirm,
  ProcessingIndicator,
  ObjectActionBar,
  RowActionMenu,
  FormActionBar,
  cn,
  type EntityAction,
  SaveErrorBanner,
  INFORMATION_TABLE_ROW_CLASS,
  INFORMATION_TABLE_VISIBLE_ROWS,
  RowTitleButton,
} from "@production-ready/ui";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  KeyRound,
  LockKeyholeOpen,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  ShieldCheck,
  Trash2,
  UserCheck,
  UserRound,
  UserX,
} from "lucide-react";

import { useUnsavedChangesGuard } from "../guards/useUnsavedChangesGuard";
import { fieldActionRowWidthClasses } from "../oci/InputActionField";
import { useRequestScope } from "../oci/useRequestScope";
import { t } from "./messages";
import { roleDisplayName } from "./RoleNameBadges";
import { userDisplayName } from "./userDisplayName";
import {
  SECURITY_NEW_ENTITY_ID,
  SecurityDetailField,
  SecurityEditorFallback,
  SecuritySearchField,
  SecurityClearSearchAction,
  SecurityListPagination,
  describeErrorMessageOnly,
  isAbortError,
  mapFieldErrors,
  nextUserRoleIds,
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
  type AssignedRole,
  type DescribeApiError,
  type SecurityRole,
  type SecurityUser,
  type UserManagementApi,
} from "./types";

export interface UserManagementPageProps {
  api: UserManagementApi;
  /** ユーザーの作成・編集・無効化などを行えるか（製品の権限判定）。false なら同じエディタを読み取り専用で出す。 */
  canManage: boolean;
  /** ログイン中のユーザー。自分自身は削除できない。 */
  currentUserUuid?: string | null;
  /** 製品の API エラーから入力項目のエラーとエラーコードを取り出す。 */
  describeError?: DescribeApiError;
}

/** 一覧（null）・新規作成・編集・URL の対象が無い（読み込み中を含む）。 */
type UserView = "list" | "create" | "edit" | "missing";

interface ResetPasswordError {
  userUuid: string;
  message: string;
}

/** 作成・リセットで発行した一時パスワード（そのユーザーのエディタの「サインインの状態」に出す）。 */
interface IssuedPassword {
  userUuid: string;
  password: string;
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

function compareText(left: string, right: string, direction: DataTableSort["direction"]) {
  return compareNames(left, right, direction);
}

function assignedRoleLabel(role: AssignedRole) {
  // ロール名で出す。参照できない（削除済みの）ロールは ID（UUID）を出さない（#1682）。
  const name = roleDisplayName(role);
  if (role.deleted || name === roleDisplayName(null)) return name;
  return role.archived ? t("security.users.archivedRoleLabel", { role: name }) : name;
}

function userStatusLabel(user: SecurityUser) {
  return user.status === "ACTIVE" ? t("security.common.active") : t("security.common.disabled");
}

/** 状態の文字（狭い画面の行の補足・検索に使う）。有効 / 無効と、ロック中・パスワード変更待ち。 */
function userStatusSummary(user: SecurityUser) {
  return [
    userStatusLabel(user),
    user.locked_until ? t("security.users.locked") : "",
    user.force_password_change ? t("security.users.passwordChangePending") : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

/**
 * ユーザー管理（3製品共通。NL2SQL の画面を移設。#206）。全幅の一覧 → 全幅のエディタ（A 型。#4635）。
 * 開いている対象は URL の `?user=`（`new` は新規作成）が唯一の情報源。
 */
export function UserManagementPage({
  api,
  canManage,
  currentUserUuid = null,
  describeError = describeErrorMessageOnly,
}: UserManagementPageProps) {
  const confirm = useConfirm();
  const [users, setUsers] = useState<SecurityUser[]>([]);
  const [roles, setRoles] = useState<SecurityRole[]>([]);
  const [loading, setLoading] = useState(true);
  // 「最新情報を取得」を押した読み直しの間だけ、そのボタンを回す。初回の読込・エラーからの再試行は
  // 押していないので、一覧の読込表示がスピナーを出し、「最新情報を取得」は disabled だけにする（#819）。
  const [refreshRequested, setRefreshRequested] = useState(false);
  const initialLoading = loading && !refreshRequested;
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [formError, setFormError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<UserFieldErrors>({});
  const selection = useSecurityEditorSelection("user");
  const target = selection.selectedId;
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<DataTableSort>({ key: "user", direction: "asc" });
  const [draft, setDraft] = useState<UserDraftState>(EMPTY_DRAFT);
  const [baseline, setBaseline] = useState<UserDraftState>(EMPTY_DRAFT);
  // 今の draft がどの対象のものか（"new" / user_uuid / null）。URL の対象が変わったら入れ直す。
  const [draftFor, setDraftFor] = useState<string | null>(null);
  // 最後に開いた対象（一覧へ戻ったときに、その行のページとフォーカスを戻す）。
  const [lastOpenedId, setLastOpenedId] = useState<string | null>(null);
  const [unlockingUserId, setUnlockingUserId] = useState<string | null>(null);
  const [copyPasswordError, setCopyPasswordError] = useState("");
  const [resettingUserId, setResettingUserId] = useState<string | null>(null);
  const [statusChangingUserId, setStatusChangingUserId] = useState<string | null>(null);
  const [deletingUserId, setDeletingUserId] = useState<string | null>(null);
  const [resetPasswordError, setResetPasswordError] = useState<ResetPasswordError | null>(null);
  // 発行した一時パスワードは URL と同期するフォームの値（draft）と分けて持つ。URL の切り替えの途中の描画で
  // draft を入れ直しても消えないようにし、利用者の操作（一覧へ戻る・ほかの対象を開く・新規作成・削除）でだけ消す。
  const [issuedPassword, setIssuedPassword] = useState<IssuedPassword | null>(null);
  const formRef = useRef<HTMLFormElement | null>(null);
  const loadSequence = useRef(0);
  const loginUserIdRef = useRef<HTMLInputElement | null>(null);
  const displayNameRef = useRef<HTMLInputElement | null>(null);
  const temporaryPasswordRef = useRef<HTMLInputElement | null>(null);
  const roleGroupRef = useRef<HTMLDivElement | null>(null);
  const { abortAll, run: runScopedRequest } = useRequestScope();

  const creating = target === SECURITY_NEW_ENTITY_ID && canManage;
  const editingUser =
    target && !creating ? (users.find((user) => user.user_uuid === target) ?? null) : null;
  const view: UserView = !target ? "list" : creating ? "create" : editingUser ? "edit" : "missing";
  const draftKey = view === "create" ? SECURITY_NEW_ENTITY_ID : view === "edit" ? (editingUser?.user_uuid ?? null) : null;

  const roleById = useMemo(
    () => new Map(roles.map((role) => [role.role_id, role])),
    [roles]
  );
  const systemAdminRoleId = useMemo(
    () => roles.find((role) => role.role_code === SYSTEM_ADMIN_ROLE_CODE)?.role_id ?? null,
    [roles]
  );
  const isSystemAdminRole = (role: SecurityRole) =>
    role.role_code === SYSTEM_ADMIN_ROLE_CODE || role.role_id === systemAdminRoleId;
  // SYSTEM_ADMIN は構成管理者（.env の system_admin）だけが持ち、DB のユーザーには割り当てない。
  // 新規作成・編集のどちらでも選択肢に出さない（#1792）。
  const roleOptions = roles.filter((role) => !isSystemAdminRole(role));
  const systemAdminRoleHidden = roleOptions.length < roles.length;
  const selectKnownRoleId = (roleIds: string[]) => {
    // 選択肢に無い SYSTEM_ADMIN は選ばない（「ほかに割り当てられているロール」に出る）。
    const availableRoleIds = new Set(roleOptions.map((role) => role.role_id));
    return roleIds.find((roleId) => availableRoleIds.has(roleId)) ?? "";
  };

  const draftFromUser = (user: SecurityUser): UserDraftState => ({
    loginUserId: user.login_user_id,
    displayName: user.display_name,
    selectedRoleId: selectKnownRoleId(user.role_ids),
    temporaryPassword: "",
  });

  const resetFormMessages = () => {
    setFormError("");
    setFieldErrors({});
    setCopyPasswordError("");
    setResetPasswordError(null);
  };

  // URL の対象（新規作成・別のユーザー・一覧）が変わったら、フォームをその対象の値で入れ直す（render 中に同期する）。
  if (draftKey !== draftFor) {
    const nextDraft = editingUser && view === "edit" ? draftFromUser(editingUser) : EMPTY_DRAFT;
    setDraftFor(draftKey);
    setDraft(nextDraft);
    setBaseline(nextDraft);
    resetFormMessages();
  }
  if (target && target !== SECURITY_NEW_ENTITY_ID && target !== lastOpenedId) {
    setLastOpenedId(target);
  }

  const issuedTemporaryPassword =
    editingUser && issuedPassword?.userUuid === editingUser.user_uuid ? issuedPassword.password : "";
  // 一時パスワードの欄の操作（コピー・再発行）の失敗。欄の下に出し、欄の aria-describedby で結び付ける。
  const temporaryPasswordActionError =
    copyPasswordError ||
    (resetPasswordError?.userUuid === editingUser?.user_uuid ? (resetPasswordError?.message ?? "") : "");
  // 一時パスワードの欄（md）とコピーの操作の行の幅（#1891）。
  const temporaryPasswordRowWidth = fieldActionRowWidthClasses("md");
  // 管理の権限が無い・無効なユーザーは、同じエディタを読み取り専用で出す。
  const disabledUser = view === "edit" && editingUser?.status !== "ACTIVE";
  const userFormReadOnly = !canManage || disabledUser;
  const canSubmitUserForm = !userFormReadOnly;
  const accountActionBusy =
    resettingUserId !== null || statusChangingUserId !== null || deletingUserId !== null || unlockingUserId !== null;
  const operationBusy = busy || accountActionBusy || loading;
  // 保存を試みた回数（保存の失敗の Banner を、同じ文言の失敗でも入れ直す。#585）。
  const [submitAttempt, setSubmitAttempt] = useState(0);
  const inputReadOnly = userFormReadOnly || operationBusy;
  const isDirty = (view === "create" || view === "edit") && (
    draft.loginUserId !== baseline.loginUserId || draft.displayName !== baseline.displayName ||
    draft.selectedRoleId !== baseline.selectedRoleId ||
    (view === "create" && draft.temporaryPassword !== baseline.temporaryPassword)
  );
  const confirmLeave = async () => !operationBusy && (!isDirty || await confirm({
    title: t("security.common.discardTitle"),
    description: t("security.common.discardDescription"),
    confirmLabel: t("security.common.discardConfirm"),
    tone: "danger",
    dismissOnOverlay: false,
  }));
  useUnsavedChangesGuard(isDirty || busy || accountActionBusy, confirmLeave);
  // 破棄して一覧へ戻るとき、離脱ガードが外れた（未保存でなくなった）commit の後で履歴を戻す
  // （先に戻ると、ブラウザの戻ると同じ確認がもう一度出る）。
  const scheduleLeave = useFocusAfterCommit(!isDirty);

  const assignedRoles = useCallback((user: SecurityUser): AssignedRole[] => {
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
            // 一覧に無いロール。ID を名前の欄に入れない（#1682）。
            role_id: id,
            role_code: "",
            display_name: "",
            is_built_in: false,
            archived: true,
            deleted: true,
          };
    });
  }, [roleById]);
  // 一覧の検索・並べ替え（useMemo）から使うため、roleById が変わったときだけ作り直す。
  const roleSummary = useCallback((user: SecurityUser) => {
    const names = assignedRoles(user).map(assignedRoleLabel);
    return names.length > 0 ? names.join(", ") : t("security.common.none");
  }, [assignedRoles]);

  const filteredUsers = useMemo(() => {
    const q = search.trim().toLowerCase();
    return users
      .filter((user) => {
        if (!q) return true;
        return (
          user.display_name.toLowerCase().includes(q) ||
          user.login_user_id.toLowerCase().includes(q) ||
          userStatusSummary(user).toLowerCase().includes(q) ||
          roleSummary(user).toLowerCase().includes(q)
        );
      })
      .sort((left, right) => {
        if (sort.key === "roles") return compareText(roleSummary(left), roleSummary(right), sort.direction);
        if (sort.key === "status") return compareText(userStatusLabel(left), userStatusLabel(right), sort.direction);
        // 1 列目は表示名を出すため、表示名で並べる（#1696）。
        return compareText(userDisplayName(left), userDisplayName(right), sort.direction);
      });
  }, [roleSummary, search, sort, users]);

  // 一覧は 25 件ずつのページ（表示件数は選べる）。検索語・並べ替えが変わったら 1 ページ目へ戻す（#1891）。
  // エディタから戻ったときは、開いていた行のあるページを出す。
  const userList = usePagedSelectionList({
    items: filteredUsers,
    getKey: (user) => user.user_uuid,
    selectedId: lastOpenedId,
    active: view === "list",
    ready: !loading,
    resetKey: `${search}\u0000${sort.key}\u0000${sort.direction}`,
    pagingKey: "users",
  });
  const userListCount = securityListCount(users.length, filteredUsers.length, search);
  useReturnFocusToRow(view === "list", lastOpenedId);

  const load = async (announce = false) => {
    if (busy || accountActionBusy) return;
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    setLoading(true);
    setRefreshRequested(announce);
    setLoadError("");
    setActionError("");
    await requestData(sequence, announce);
  };

  // 製品が描画のたびに新しい api オブジェクトを渡しても初回読込をやり直さないよう、最新の api は ref で読む。
  const apiRef = useRef(api);
  useLayoutEffect(() => {
    apiRef.current = api;
  });

  // 読込中・エラー表示の初期化は呼び出し側で行う（初回表示は初期 state が読込中）。
  // 初回読込の effect から使うため、安定した参照にする（依存は useRequestScope の安定した run だけ）。
  const requestData = useCallback(async (sequence: number, announce: boolean) => {
    try {
      await runScopedRequest(async (signal) => {
        const [userRows, roleRows] = await Promise.all([
          apiRef.current.users({ signal }),
          apiRef.current.roles(false, { signal }),
        ]);
        if (signal.aborted || sequence !== loadSequence.current) return;
        setUsers(userRows);
        setRoles(roleRows.filter((role) => !role.archived));
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
  }, [runScopedRequest]);

  // abortAll / requestData は安定した参照なので、この effect はマウント時に 1 回だけ動く。
  useEffect(() => {
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    void requestData(sequence, false);
    return () => {
      loadSequence.current += 1;
      abortAll();
    };
  }, [abortAll, requestData]);

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

  // 送信中は欄が disabled のため、操作できる状態を commit した後に移す（#424）。
  const scheduleFocus = useFocusAfterCommit(!inputReadOnly);
  const focusFirstFieldError = (errors: UserFieldErrors) => {
    scheduleFocus(() => {
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

  const openUser = (user: SecurityUser) => {
    if (operationBusy) return;
    setActionError("");
    setIssuedPassword(null);
    selection.open(user.user_uuid);
  };

  const startCreate = () => {
    setActionError("");
    setIssuedPassword(null);
    selection.open(SECURITY_NEW_ENTITY_ID);
  };

  /** 一時パスワード（作成・リセットの結果）を持たせて、そのユーザーのエディタを開く。 */
  const showUserWithPassword = (user: SecurityUser, temporaryPassword: string, how: "open" | "replace") => {
    setIssuedPassword({ userUuid: user.user_uuid, password: temporaryPassword });
    setCopyPasswordError("");
    if (how === "open") selection.open(user.user_uuid);
    else selection.replace(user.user_uuid);
  };

  const returnToList = async () => {
    if (!(await confirmLeave())) return;
    setActionError("");
    setIssuedPassword(null);
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
    if (operationBusy || !canSubmitUserForm) return;
    setSubmitAttempt((current) => current + 1);
    setFormError("");
    setFieldErrors({});
    setActionError("");
    // 未入力は送信前に欄の下へ出し、最初のエラーの欄へフォーカスする（noValidate。#531）。
    const requiredErrors: UserFieldErrors = {};
    if (view === "create" && !draft.loginUserId.trim()) {
      requiredErrors.loginUserId = t("security.users.loginUserIdRequired");
    }
    if (!draft.displayName.trim()) {
      requiredErrors.displayName = t("security.users.displayNameRequired");
    }
    if (!draft.selectedRoleId) {
      requiredErrors.selectedRoleId = t("security.users.roleRequired");
    }
    if (Object.keys(requiredErrors).length > 0) {
      setFieldErrors(requiredErrors);
      focusFirstFieldError(requiredErrors);
      return;
    }
    const submittedRoleId = draft.selectedRoleId;
    setBusy(true);
    try {
      if (view === "edit") {
        if (!editingUser) return;
        // 選び直したロールだけを置き換え、選択欄に出ないほかのロールは残す（#1050）。
        const updated = await api.updateUser({
          ...editingUser,
          display_name: draft.displayName,
          role_ids: nextUserRoleIds(editingUser.role_ids, baseline.selectedRoleId, submittedRoleId),
        });
        setUsers((rows) => rows.map((row) => (row.user_uuid === updated.user_uuid ? updated : row)));
        // 応答のロールの並びは選んだ順と限らないため、保存したロールが残っていればそれを選択のままにする。
        const savedRoleId = updated.role_ids.includes(submittedRoleId)
          ? submittedRoleId
          : selectKnownRoleId(updated.role_ids);
        setBaseline((current) => ({
          ...current,
          displayName: updated.display_name,
          selectedRoleId: savedRoleId,
        }));
        setDraft((current) => ({
          ...current,
          displayName: updated.display_name,
          selectedRoleId: savedRoleId,
        }));
        toast.success(t("security.common.saved"));
      } else {
        const created = await api.createUser({
          login_user_id: draft.loginUserId,
          display_name: draft.displayName,
          role_ids: [submittedRoleId],
          temporary_password: draft.temporaryPassword || undefined,
        });
        setUsers((rows) => [...rows, created.user]);
        // 作ったユーザーのエディタへ置き換え、発行した一時パスワードを「サインインの状態」に出す。
        showUserWithPassword(created.user, created.temporary_password, "replace");
        toast.success(t("security.common.saved"));
      }
    } catch (cause) {
      const details = describeError(cause);
      const nextErrors = mapFieldErrors(
        details,
        USER_POINTER_TO_FIELD,
        (problem, problemDetails) =>
          problemDetails.code === "SECURITY_USER_LOGIN_ID_CONFLICT" &&
          problem.pointer === "/login_user_id"
            ? t("security.users.loginUserIdConflict")
            : problem.message
      );
      setFieldErrors(nextErrors);
      setFormError(
        unmappedErrorMessage(cause, details, USER_POINTER_TO_FIELD, t("security.common.saveError"))
      );
      if (Object.keys(nextErrors).length > 0) {
        focusFirstFieldError(nextErrors);
      }
    } finally {
      setBusy(false);
    }
  };

  const handleToggleStatus = async (user: SecurityUser) => {
    if (operationBusy) return;
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
      const updated = await api.setUserEnabled(user, enabling);
      setUsers((rows) => rows.map((row) => (row.user_uuid === updated.user_uuid ? updated : row)));
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
    if (user.status !== "ACTIVE" || operationBusy) return;
    if (
      !(await confirm({
        title: t("security.users.resetPassword"),
        description: t("security.users.resetConfirm"),
        tone: "warning",
      }))
    ) {
      return;
    }
    setActionError("");
    setCopyPasswordError("");
    setResetPasswordError(null);
    setResettingUserId(user.user_uuid);
    try {
      const result = await api.resetPassword(user.user_uuid);
      setUsers((rows) => rows.map((row) => (row.user_uuid === result.user.user_uuid ? result.user : row)));
      if (view === "edit" && editingUser?.user_uuid === result.user.user_uuid) {
        setIssuedPassword({ userUuid: result.user.user_uuid, password: result.temporary_password });
      } else {
        // 一覧の行からリセットしたときは、そのユーザーのエディタを開いて一時パスワードを出す。
        showUserWithPassword(result.user, result.temporary_password, "open");
      }
      toast.success(t("security.users.oneTimePassword.resetSuccess"));
    } catch (cause) {
      const message =
        cause instanceof Error && cause.message.trim()
          ? cause.message
          : t("security.users.oneTimePassword.resetError");
      setResetPasswordError({ userUuid: user.user_uuid, message });
      // 一覧の行からの失敗は一覧の上に出す（エディタでは「サインインの状態」の欄の下）。
      if (view === "list") setActionError(message);
    } finally {
      setResettingUserId(null);
    }
  };

  const handleUnlock = async (user: SecurityUser) => {
    if (operationBusy) return;
    setUnlockingUserId(user.user_uuid);
    setActionError("");
    try {
      const updated = await api.unlockUser(user.user_uuid);
      setUsers((rows) => rows.map((row) => (row.user_uuid === updated.user_uuid ? updated : row)));
      toast.success(t("security.common.saved"));
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : t("security.common.saveError"));
    } finally {
      setUnlockingUserId(null);
    }
  };

  const canDeleteUser = (user: SecurityUser) =>
    user.status === "DISABLED" &&
    !user.is_bootstrap_admin &&
    currentUserUuid !== user.user_uuid;

  const handleDelete = async (user: SecurityUser) => {
    if (operationBusy || !canDeleteUser(user)) return;
    if (
      !(await confirm({
        title: t("security.users.delete"),
        description: t("security.users.deleteConfirm", { name: userDisplayName(user) }),
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
      await api.deleteUser(user);
      setUsers((rows) => rows.filter((row) => row.user_uuid !== user.user_uuid));
      // 削除した対象のエディタは閉じて一覧へ戻す（ほかの対象へ置き換えない）。
      if (target === user.user_uuid) selection.replace(null);
      setLastOpenedId(null);
      setIssuedPassword(null);
      toast.success(t("security.users.deleteSuccess", { name: userDisplayName(user) }));
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

  /** 対象への操作（行のメニューとエディタの ObjectActionBar で同じ定義。page-archetypes.md §0-6）。 */
  const accountActions = (user: SecurityUser): EntityAction[] =>
    canManage
      ? [
          {
            id: "reset-password",
            label: t("security.users.resetPassword"),
            icon: KeyRound,
            onSelect: () => handleResetPassword(user),
            visible: user.status === "ACTIVE" && !user.is_bootstrap_admin,
            loading: resettingUserId === user.user_uuid,
            // 押したボタンは loading（aria-disabled）でフォーカスを保つ。ネイティブの disabled にしない（#355 / #835）。
            disabled: operationBusy && !(resettingUserId === user.user_uuid),
          },
          {
            id: "unlock",
            label: t("security.users.unlock"),
            icon: LockKeyholeOpen,
            visible: Boolean(user.locked_until),
            loading: unlockingUserId === user.user_uuid,
            disabled: operationBusy && !(unlockingUserId === user.user_uuid),
            onSelect: () => handleUnlock(user),
          },
          {
            id: user.status === "ACTIVE" ? "disable" : "enable",
            label: user.status === "ACTIVE" ? t("security.users.disable") : t("security.users.enable"),
            icon: user.status === "ACTIVE" ? UserX : UserCheck,
            tone: user.status === "ACTIVE" ? "danger" : "default",
            loading: statusChangingUserId === user.user_uuid,
            disabled: operationBusy && !(statusChangingUserId === user.user_uuid),
            onSelect: () => handleToggleStatus(user),
          },
          {
            id: "delete",
            label: t("security.users.delete"),
            icon: Trash2,
            tone: "danger",
            visible: canDeleteUser(user),
            loading: deletingUserId === user.user_uuid,
            disabled: operationBusy && !(deletingUserId === user.user_uuid),
            onSelect: () => handleDelete(user),
          },
        ]
      : [];

  const selectRole = (roleId: string) => {
    if (userFormReadOnly) return;
    updateDraftField("selectedRoleId", roleId);
  };

  // 全幅の表（#4635）。md 未満は先頭の列だけを残し、割り当てロールと状態は名前の下の補足 1 行に畳む（横にスクロールさせない）。
  const userColumns: Array<DataTableColumn<SecurityUser>> = [
    {
      key: "user",
      header: t("security.users.column.user"),
      sortable: true,
      rowHeader: true,
      className: "align-top",
      render: (user) => (
        <RowTitleButton
          title={userDisplayName(user)}
          subtitle={
            <>
              {/* ログイン ID は ID の書体で 2 行目に出す（一覧で見分ける。#4635）。 */}
              <code className="block break-all font-mono">{user.login_user_id}</code>
              <span className="block md:hidden" data-testid="security-users-row-summary">
                {`${roleSummary(user)} · ${userStatusSummary(user)}`}
              </span>
            </>
          }
          href={selection.hrefFor(user.user_uuid)}
          data-security-row-id={user.user_uuid}
          aria-label={t("security.users.showUser", { name: userDisplayName(user) })}
          onClick={(event) => {
            event.stopPropagation();
            openUser(user);
          }}
        />
      ),
    },
    {
      key: "roles",
      header: t("security.users.column.roles"),
      sortable: true,
      headerClassName: "hidden md:table-cell",
      className: "hidden align-top md:table-cell",
      render: (user) => <span className="break-words text-sm">{roleSummary(user)}</span>,
    },
    {
      key: "status",
      header: t("security.common.status"),
      sortable: true,
      headerClassName: "hidden w-[16rem] md:table-cell",
      className: "hidden align-top md:table-cell",
      render: (user) => <UserStatusBadges user={user} />,
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
            render: (user: SecurityUser) => (
              <RowActionMenu
                actions={accountActions(user)}
                ariaLabel={`${t("security.common.actions")}: ${userDisplayName(user)}`}
                testId={`security-users-row-actions-${user.user_uuid}`}
              />
            ),
          } satisfies DataTableColumn<SecurityUser>,
        ]
      : []),
  ];

  const initialLoadFailed = Boolean(loadError) && users.length === 0 && roles.length === 0;
  // 編集中のユーザーの、選択欄で選ばれていないほかのロール（保存しても変わらない。#1050）。
  const otherAssignedRoles =
    view === "edit" && editingUser
      ? assignedRoles(editingUser).filter(
          (role) => role.role_id !== baseline.selectedRoleId && role.role_id !== draft.selectedRoleId,
        )
      : [];
  const back = { label: t("security.common.backToList"), onClick: () => void returnToList(), disabled: operationBusy, testId: "security-users-back" };
  const errorBanners = (
    <>
      {loadError && !initialLoadFailed ? <Banner severity="danger">{loadError}</Banner> : null}
      {actionError ? <Banner severity="danger">{actionError}</Banner> : null}
    </>
  );

  if (view === "missing") {
    return (
      <>
        <PageHeader wide title={t("nav.securityUsers")} back={back} />
        <PageBody wide className="grid gap-4">
          {initialLoadFailed ? (
            <ErrorState message={loadError} onRetry={() => void load()} />
          ) : (
            <SecurityEditorFallback
              loading={loading}
              loadingLabel={t("security.users.loading")}
              title={t("security.users.notFoundTitle")}
              hint={t("security.users.notFoundHint")}
              onBack={() => selection.close()}
              testId="security-users-detail-not-found"
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
          title={view === "edit" && editingUser ? userDisplayName(editingUser) : t("security.users.form.create")}
          subtitle={view === "create" ? t("security.users.formHint") : undefined}
          status={view === "edit" && editingUser ? <UserStatusBadges user={editingUser} /> : undefined}
          // 一覧へ戻るは左上（#618）。確定（作成・保存）はフォームの末尾の FormActionBar（#1786）。
          back={back}
        />
        <PageBody wide className="grid grid-cols-1 gap-5">
          {errorBanners}
          {canManage && disabledUser ? (
            <Banner severity="info">{t("security.users.disabledReadonly")}</Banner>
          ) : null}
          {editingUser && assignedRoles(editingUser).some((role) => role.archived) ? (
            <Banner severity="warning">{t("security.users.archivedRoleNotice")}</Banner>
          ) : null}
          <Card data-testid="security-users-editor">
            <CardHeader className="flex-row flex-wrap items-start justify-between gap-3">
              <CardTitle id="security-users-form-heading" className="flex items-center gap-2">
                <UserRound size={20} aria-hidden="true" />
                {t("security.users.section.basic")}
              </CardTitle>
              {view === "edit" && editingUser && canManage ? (
                // 対象への操作は最初のカードの見出しの右の 1 か所（#618）。
                <ObjectActionBar
                  actions={accountActions(editingUser)}
                  ariaLabel={t("security.users.editActions")}
                  testId="security-users-object-actions"
                />
              ) : null}
            </CardHeader>
            <CardContent>
              <form
                ref={formRef}
                className="grid gap-6"
                onSubmit={handleSubmit}
                aria-labelledby="security-users-form-heading"
                noValidate
              >
                {/* 欄の並びは FormGrid（最大 60rem・置き場所 42rem 以上で 2 列）。幅は値の種類の段（#1891）。 */}
                <FormGrid>
                  <TextField
                    ref={loginUserIdRef}
                    id="security-user-login-user-id"
                    label={t("security.users.loginUserId")}
                    required={view === "create"}
                    maxLength={64}
                    width="md"
                    disabled={view === "edit" || inputReadOnly}
                    error={fieldErrors.loginUserId}
                    autoComplete="off"
                    value={draft.loginUserId}
                    onValueChange={(value) => updateDraftField("loginUserId", value)}
                  />
                  <TextField
                    ref={displayNameRef}
                    id="security-user-display-name"
                    label={t("security.users.displayName")}
                    required={!userFormReadOnly}
                    width="md"
                    disabled={inputReadOnly}
                    error={fieldErrors.displayName}
                    value={draft.displayName}
                    onValueChange={(value) => updateDraftField("displayName", value)}
                  />
                  {view === "create" ? (
                    <TextField
                      ref={temporaryPasswordRef}
                      id="security-user-temporary-password"
                      label={t("security.users.tempPassword")}
                      type="password"
                      width="md"
                      disabled={inputReadOnly}
                      error={fieldErrors.temporaryPassword}
                      autoComplete="new-password"
                      value={draft.temporaryPassword}
                      onValueChange={(value) => updateDraftField("temporaryPassword", value)}
                      data-testid="security-user-temporary-password"
                    />
                  ) : null}
                </FormGrid>
                <fieldset className="grid gap-2 border-t border-border pt-5" disabled={inputReadOnly}>
                  {/* 必須は下の radiogroup の aria-required で伝えるので、legend のタグは読み上げない。 */}
                  <FieldLegend id="security-users-role-legend" required={!userFormReadOnly} requiredAnnouncedByControl>
                    {t("security.users.roles")}
                  </FieldLegend>
                  {roleOptions.length === 0 ? (
                    <p className="text-sm text-fg-muted">{t("security.users.noRole")}</p>
                  ) : (
                    <div
                      ref={roleGroupRef}
                      className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3"
                      role="radiogroup"
                      aria-labelledby="security-users-role-legend"
                      aria-required={userFormReadOnly ? undefined : "true"}
                      aria-invalid={fieldErrors.selectedRoleId ? "true" : undefined}
                      aria-describedby={
                        [
                          systemAdminRoleHidden ? "security-users-role-system-admin-note" : "",
                          fieldErrors.selectedRoleId ? "security-users-role-error" : "",
                        ].filter(Boolean).join(" ") || undefined
                      }
                    >
                      {roleOptions.map((role) => {
                        const disabled = userFormReadOnly;
                        const selected = draft.selectedRoleId === role.role_id;
                        return (
                          <label
                            key={role.role_id}
                            data-surface-tint={disabled ? "neutral" : selected ? "info" : undefined}
                            className={cn(
                              "flex min-h-11 items-start gap-2 rounded-md border p-2.5 text-sm transition-colors",
                              disabled
                                ? cn("cursor-not-allowed bg-surface-hover text-fg-muted", selected && "border-accent-emphasis")
                                : selected
                                  ? "cursor-pointer border-accent-emphasis bg-info-subtle"
                                  : "cursor-pointer border-border hover:bg-surface-hover"
                            )}
                          >
                            <input
                              className="mt-0.5 h-4 w-4 accent-accent-emphasis disabled:cursor-not-allowed"
                              type="radio"
                              name="security-users-role"
                              value={role.role_id}
                              checked={selected}
                              disabled={disabled}
                              onChange={() => selectRole(role.role_id)}
                            />
                            <span className="min-w-0">
                              {/* ロール名だけを出す（コード・ID は出さない。#1682） */}
                              <span className="block break-words font-medium">{roleDisplayName(role)}</span>
                            </span>
                          </label>
                        );
                      })}
                    </div>
                  )}
                  {systemAdminRoleHidden ? (
                    <p
                      id="security-users-role-system-admin-note"
                      className="text-sm text-fg-muted"
                      data-testid="security-users-role-system-admin-note"
                    >
                      {t("security.users.systemAdminConfigOnly")}
                    </p>
                  ) : null}
                  <FieldError id="security-users-role-error" message={fieldErrors.selectedRoleId} />
                  {otherAssignedRoles.length > 0 ? (
                    <p className="text-sm text-fg-muted" data-testid="security-users-other-roles">
                      {t("security.users.otherRoles", {
                        roles: otherAssignedRoles.map(assignedRoleLabel).join(", "),
                      })}
                    </p>
                  ) : null}
                </fieldset>
                {/* 保存の失敗は確定の行の直上の 1 か所だけ（messaging.md §3.3.1。#585 / #1786）。 */}
                <SaveErrorBanner
                  message={formError}
                  attemptKey={submitAttempt}
                  testId="security-users-save-error"
                />
                {canSubmitUserForm ? (
                  // 確定はフォームの末尾の左（作成・保存 → 変更を破棄）。画面の下端に貼り付けない（#1786 / #2715）。
                  <FormActionBar
                    ariaLabel={t("security.users.formActionsLabel")}
                    testId="security-users-form-actions"
                    primaryActions={[
                      {
                        id: view === "edit" ? "save" : "create",
                        label: view === "edit" ? t("security.common.save") : t("security.common.submitCreate"),
                        icon: view === "edit" ? Save : Plus,
                        type: "submit",
                        loading: busy,
                        // 押したボタンは loading（aria-disabled）でフォーカスを保つ。ネイティブの disabled にしない（#355 / #835）。
                        disabled: operationBusy && !busy,
                        testId: "security-users-submit",
                      },
                    ]}
                    secondaryActions={[
                      {
                        id: "discard",
                        label: t("security.common.discardChanges"),
                        icon: RotateCcw,
                        disabled: !isDirty || operationBusy,
                        testId: "security-users-discard",
                        onClick: discardChanges,
                      },
                    ]}
                  />
                ) : null}
              </form>
            </CardContent>
          </Card>

          {view === "edit" && editingUser ? (
            <Card data-testid="security-users-sign-in">
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <ShieldCheck size={20} aria-hidden="true" />
                  {t("security.users.section.signIn")}
                </CardTitle>
              </CardHeader>
              <CardContent className="grid gap-4">
                <dl className="grid gap-3 md:grid-cols-3">
                  <SecurityDetailField label={t("security.common.status")}>
                    {userStatusLabel(editingUser)}
                  </SecurityDetailField>
                  <SecurityDetailField label={t("security.users.lock")}>
                    {editingUser.locked_until ? t("security.users.locked") : t("security.common.none")}
                  </SecurityDetailField>
                  <SecurityDetailField label={t("security.users.forceChange")}>
                    {editingUser.force_password_change ? t("security.common.yes") : t("security.common.no")}
                  </SecurityDetailField>
                </dl>
                {issuedTemporaryPassword || temporaryPasswordActionError ? (
                  // 発行した一時パスワード（作成・リセットの直後だけ）とコピーの操作の行。
                  // エラーは操作の下端がずれないよう footer に出す（#613 / #631）。
                  <FieldActionRow
                    className={temporaryPasswordRowWidth.row}
                    fit={temporaryPasswordRowWidth.fit}
                    actions={
                      <CopyButton
                        text={issuedTemporaryPassword}
                        label={t("security.users.oneTimePassword.copy")}
                        copiedMessage={t("common.action.copied")}
                        failedMessage={t("security.users.oneTimePassword.copyError")}
                        disabled={userFormReadOnly || !issuedTemporaryPassword}
                        onCopied={() => setCopyPasswordError("")}
                        onCopyError={setCopyPasswordError}
                        testId="security-user-temporary-password-copy"
                      />
                    }
                    footer={
                      <div
                        className={cn("space-y-1.5", temporaryPasswordRowWidth.footer)}
                        id="security-user-temporary-password-action-error"
                        data-testid="security-user-temporary-password-error"
                      >
                        <FormStatus tone="danger" message={temporaryPasswordActionError} />
                      </div>
                    }
                  >
                    <TextField
                      id="security-user-temporary-password"
                      label={t("security.users.oneTimePassword.valueLabel")}
                      width="md"
                      readOnly
                      // 無効なユーザーでは発行済みの一時パスワードを使わせない（コピーも止める）。
                      disabled={userFormReadOnly}
                      aria-describedby={temporaryPasswordActionError ? "security-user-temporary-password-action-error" : undefined}
                      autoComplete="off"
                      value={issuedTemporaryPassword}
                      data-testid="security-user-temporary-password"
                    />
                  </FieldActionRow>
                ) : null}
              </CardContent>
            </Card>
          ) : null}
        </PageBody>
      </>
    );
  }

  return (
    <>
      <PageHeader
        wide
        title={t("nav.securityUsers")}
        subtitle={t("security.users.subtitle")}
        actions={[
          ...(canManage && !initialLoadFailed
            ? [
                {
                  id: "create-user",
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
        actionsLabel={t("security.users.actionsLabel")}
        actionsTestId="security-users-actions"
      />
      <PageBody wide className="grid grid-cols-1 gap-4">
        {errorBanners}
        {initialLoadFailed ? (
          <ErrorState message={loadError} onRetry={() => void load()} />
        ) : (
          <section className="grid min-w-0 gap-3" aria-label={t("security.users.list")} data-testid="security-users-list">
            {/* 件数はツールバーの右の ListCount 1 か所（#1891）。件数の読み上げは検索欄の resultCountLabel が行う。 */}
            <ListToolbar
              testId="security-users-toolbar"
              search={
                <SecuritySearchField
                  label={t("security.common.search")}
                  placeholder={t("security.users.searchPlaceholder")}
                  value={search}
                  testId="security-users-search"
                  disabled={operationBusy}
                  resultCountLabel={listCountText(userListCount)}
                  onChange={setSearch}
                />
              }
              count={{ ...userListCount, live: false, testId: "security-users-count" }}
            />
            {/* 再読み込みは前の行を残し、ボタンの loading だけで示す。経過時間と Skeleton は最初の読み込みだけ（#4649）。 */}
            {initialLoading ? (
              <ProcessingIndicator
                active
                label={t("security.users.loading")}
                operationKey="security-users-load"
                placement="panel"
                testId="security-users-loading"
              />
            ) : null}
            <div className="grid gap-2">
              <DataTable
                loading={initialLoading}
                rows={userList.pageItems}
                sort={sort}
                onSortChange={(next) => { if (!operationBusy) setSort(next); }}
                // 行の操作以外の領域のクリックでエディタを開く（page-archetypes.md §0-7）。キーボードは名前のリンク。
                onRowClick={openUser}
                getRowKey={(user) => user.user_uuid}
                rowProps={(user) => ({ className: INFORMATION_TABLE_ROW_CLASS, "data-testid": `security-users-row-${user.user_uuid}` })}
                ariaLabel={t("security.users.list")}
                testId="security-users-grid"
                scrollAriaLabel={t("security.common.listScrollLabel", { list: t("security.users.list") })}
                scrollTestId="security-users-scroll-region"
                tableClassName="w-full"
                stickyHeader
                visibleRows={INFORMATION_TABLE_VISIBLE_ROWS}
                growToViewport
                empty={<EmptyState title={search ? t("security.users.noResultsTitle") : t("security.common.empty")} hint={search ? t("security.users.noResultsHint") : undefined} action={search ? <SecurityClearSearchAction onClear={() => setSearch("")} /> : undefined} />}
                columns={userColumns}
              />
              <SecurityListPagination
                list={userList}
                ariaLabel={t("security.users.pagination")}
                testId="security-users-pagination"
                disabled={operationBusy}
              />
            </div>
          </section>
        )}
      </PageBody>
    </>
  );
}

/** ユーザーの状態（有効 / 無効、ロック中、パスワード変更待ち）。一覧の状態の列とエディタの見出しで使う。 */
function UserStatusBadges({ user }: { user: SecurityUser }) {
  return (
    <div className="flex flex-wrap gap-1">
      <StatusBadge
        variant={user.status === "ACTIVE" ? "success" : "neutral"}
        label={userStatusLabel(user)}
      />
      {user.locked_until ? <StatusBadge variant="warning" label={t("security.users.locked")} /> : null}
      {user.force_password_change ? (
        <StatusBadge variant="info" label={t("security.users.passwordChangePending")} />
      ) : null}
    </div>
  );
}
