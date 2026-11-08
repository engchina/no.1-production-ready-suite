import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { LockKeyhole, Plus, RefreshCw, RotateCcw, Save, ShieldCheck } from "lucide-react";
import { Link } from "react-router-dom";
import {
  compareNames,
  Banner,
  BulkSelectionActions,
  Button,
  ButtonLink,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  ListPicker,
  type ListPickerItem,
  DataTable,
  EmptyState,
  FieldActionRow,
  FieldError,
  FormActionBar,
  ListToolbar,
  listCountText,
  PageBody,
  PageHeader,
  ProcessingIndicator,
  StatusBadge,
  TextField,
  isSubmitEnter,
  toast,
  useConfirm,
  type DataTableColumn,
  type DataTableSort,
  SaveErrorBanner,
  INFORMATION_TABLE_ROW_CLASS,
  INFORMATION_TABLE_VISIBLE_ROWS,
  apiErrorMessage,
  cn,
} from "@production-ready/ui";

import { formatMessage } from "../auth/messages";
import { useUnsavedChangesGuard } from "../guards/useUnsavedChangesGuard";
import { fieldActionRowWidthClasses } from "../oci/InputActionField";
import { useRequestScope } from "../oci/useRequestScope";
import { RoleStatusBadges } from "../users-roles/RoleManagementPage";
import { RoleNameRowTitleButton, roleDisplayName } from "../users-roles/RoleNameBadges";
import { t as usersRolesText } from "../users-roles/messages";
import {
  SecurityEditorFallback,
  SecuritySearchField,
  SecurityClearSearchAction,
  SecurityListPagination,
  isAbortError,
  securityFilteredCountWithSelected,
  securityListCount,
  useFocusAfterCommit,
  usePagedSelectionList,
  useReturnFocusToRow,
  useSecurityEditorSelection,
} from "../users-roles/shared";
import { SYSTEM_ADMIN_ROLE_CODE } from "../users-roles/types";
import { ROLE_PERMISSIONS_MESSAGES, type RolePermissionsMessages } from "./messages";
import type {
  PermissionDefinition,
  PermissionRole,
  RolePermissionCustomIdOptions,
  RolePermissionTargetItem,
  RolePermissionTargetQuery,
  RolePermissionTargetSection,
  RolePermissionsApi,
  RolePermissionsDraft,
} from "./types";

const EMPTY_DRAFT: RolePermissionsDraft = { permissions: [], targets: {} };
const NO_TARGETS: never[] = [];

function compareText(left: string, right: string, direction: DataTableSort["direction"]) {
  return compareNames(left, right, direction);
}

function compareNumber(left: number, right: number, direction: DataTableSort["direction"]) {
  const result = left - right;
  return direction === "asc" ? result : -result;
}

function normalizedRole<R extends PermissionRole>(role: R): R {
  return { ...role, permissions: role.permissions ?? [] };
}

/** 対象の表示名。補足があれば「名前 (補足)」。 */
export function targetItemLabel(item: RolePermissionTargetItem) {
  return [item.name, item.secondary ? `(${item.secondary})` : ""].filter(Boolean).join(" ");
}

/** 候補の 1 ページの件数（#608。NL2SQL の追加読み込みと同じ 50 件ずつ）。 */
export const TARGET_PAGE_SIZE = 50;
/** `ids` の問い合わせ 1 回で読む ID の数（3 製品の backend の上限 100）。 */
const TARGET_IDS_PER_REQUEST = 100;

/**
 * ロールに選択済みの対象の名前を、`ids` の問い合わせで読む（#608）。候補は全件を読まないので、
 * 一覧の検索・詳細の名前・「選択中だけ表示」に要る分だけを 100 件ずつに分けて読む。
 * 候補の一部だけ読めた理由（#240）は、最初の 1 つを `warning` に返す。
 */
export async function resolveTargetItems(
  target: Pick<RolePermissionTargetSection, "query">,
  ids: readonly string[],
  signal: AbortSignal,
): Promise<{ items: RolePermissionTargetItem[]; warning: string }> {
  const unique = [...new Set(ids)];
  const chunks: string[][] = [];
  for (let index = 0; index < unique.length; index += TARGET_IDS_PER_REQUEST) {
    chunks.push(unique.slice(index, index + TARGET_IDS_PER_REQUEST));
  }
  const pages = await Promise.all(
    chunks.map((chunk) => target.query({ q: "", limit: chunk.length, offset: 0, ids: chunk }, { signal })),
  );
  return {
    items: pages.flatMap((page) => page.items),
    warning: pages.map((page) => page.warning?.trim() ?? "").find(Boolean) ?? "",
  };
}

/**
 * 候補の問い合わせのクエリ文字列（#608。3 製品の backend で同じ: `q` / `limit` / `offset` / 繰り返しの `ids`）。
 * 製品の `query` はこれを対象の API の path に付けて呼ぶ。
 */
export function rolePermissionTargetSearchParams(query: RolePermissionTargetQuery): URLSearchParams {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  const q = query.q.trim();
  if (q) params.set("q", q);
  for (const id of query.ids ?? []) params.append("ids", id);
  return params;
}

/** 読んだ候補を ID で重ねる（後から読んだ方で上書きし、順序は先に読んだ順）。 */
function mergeTargetItems(
  current: readonly RolePermissionTargetItem[],
  next: readonly RolePermissionTargetItem[],
): RolePermissionTargetItem[] {
  const merged = new Map(current.map((item) => [item.id, item]));
  for (const item of next) merged.set(item.id, item);
  return [...merged.values()];
}

/**
 * 候補にない ID（直接入力した ID や、候補の取得元に現れなくなった保存済みの ID）を候補の末尾に足す。
 * 名前は ID のまま、`customStatus` があれば状態として添える（#215）。
 */
export function targetItemsWithCustomIds(
  items: readonly RolePermissionTargetItem[],
  ids: readonly string[],
  customStatus?: string,
): RolePermissionTargetItem[] {
  const known = new Set(items.map((item) => item.id));
  const extra = [...new Set(ids)]
    .filter((id) => !known.has(id))
    .map((id): RolePermissionTargetItem => (customStatus ? { id, name: id, status: customStatus } : { id, name: id }));
  return [...items, ...extra];
}

/** 直接入力した ID を検証する。前後の空白を除いた ID を返し、空・形式に合わなければ null（#215）。 */
export function normalizeCustomTargetId(value: string, pattern?: RegExp): string | null {
  const id = value.trim();
  if (!id) return null;
  if (pattern) {
    // g / y フラグ付きの RegExp でも毎回先頭から判定する。
    pattern.lastIndex = 0;
    if (!pattern.test(id)) return null;
  }
  return id;
}

/** 対象の候補。直接入力を許可する対象では、選択済みで候補にない ID も含める。 */
function targetItemsFor<R extends PermissionRole>(
  target: RolePermissionTargetSection<R>,
  items: readonly RolePermissionTargetItem[],
  selectedIds: readonly string[],
): RolePermissionTargetItem[] {
  return target.allowCustomIds
    ? targetItemsWithCustomIds(items, selectedIds, target.allowCustomIds.customStatus)
    : [...items];
}

/** 直接付けた権限から `implies` で継承される権限と、その継承元の権限名。 */
export function permissionInheritanceSources(
  directCodes: readonly string[],
  permissionByCode: Map<string, PermissionDefinition>,
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

/** 直接付けた権限と、`implies` で継承される権限を合わせた有効な権限コード。 */
export function effectivePermissionCodes(
  directCodes: readonly string[],
  permissionByCode: Map<string, PermissionDefinition>,
) {
  const codes = new Set(directCodes);
  for (const code of permissionInheritanceSources(directCodes, permissionByCode).keys()) {
    codes.add(code);
  }
  return codes;
}

/** SYSTEM_ADMIN、または有効な権限から全件が対象になるか。 */
function targetGrantsAll<R extends PermissionRole>(
  target: RolePermissionTargetSection<R>,
  roleCode: string | undefined,
  effectiveCodes: ReadonlySet<string>,
) {
  return roleCode === SYSTEM_ADMIN_ROLE_CODE || Boolean(target.grantsAll?.(effectiveCodes));
}

/** 組み込みロールとアーカイブ済みロールは権限を変更できない（backend も 409 で拒否する）。 */
function permissionsEditable(role: PermissionRole) {
  return !role.is_built_in && !role.archived;
}

/** ロールの種類（組み込み / カスタム / アーカイブ済み。ロール管理の一覧と同じ文言）。 */
function roleKindLabel(role: PermissionRole) {
  if (role.archived) return usersRolesText("security.roles.archived");
  if (role.is_built_in) return usersRolesText("security.roles.builtIn");
  return usersRolesText("security.roles.custom");
}

function canonicalDraft(value: RolePermissionsDraft) {
  return JSON.stringify({
    permissions: [...new Set(value.permissions)].sort(),
    targets: Object.fromEntries(
      Object.entries(value.targets)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, ids]) => [key, [...new Set(ids)].sort()]),
    ),
  });
}

export interface RolePermissionsPageProps<R extends PermissionRole = PermissionRole> {
  api: RolePermissionsApi<R>;
  /** 権限を編集できるか（製品の権限判定）。false なら同じエディタを読み取り専用で出す。 */
  canManage: boolean;
  /**
   * ロールに付ける「利用できる対象」（製品固有）。無ければ機能権限だけを扱う。
   * 一覧の検索を毎 render 作り直さないよう、module 定数か useMemo で安定した配列を渡す。
   */
  targets?: RolePermissionTargetSection<R>[];
  /**
   * 製品のロール管理の画面のパス。渡すと、エディタに同じロールをロール管理で開く導線（`?role=<role_id>`）を出す。
   * ロール管理を開けない利用者には渡さない。
   */
  roleManagementPath?: string;
  /** 既定の文言（日本語）の一部を上書きする。 */
  messages?: Partial<RolePermissionsMessages>;
}

/** 一覧（null）・編集・URL の対象が無い（読み込み中を含む）。 */
type PermissionView = "list" | "edit" | "missing";

/**
 * 権限管理（NL2SQL から移設。#220）。ロールごとの機能権限と、製品固有の利用できる対象を設定する。
 * ロールの作成・名称変更・アーカイブは共通のロール管理で行う。全幅の一覧 → 全幅のエディタ（A 型。#4635）。
 * 開いているロールは URL の `?role=<role_id>` が唯一の情報源（ロール管理からの導線もこの形で開く）。
 */
export function RolePermissionsPage<R extends PermissionRole = PermissionRole>({
  api,
  canManage,
  targets = NO_TARGETS,
  roleManagementPath,
  messages,
}: RolePermissionsPageProps<R>) {
  const m = { ...ROLE_PERMISSIONS_MESSAGES, ...messages };
  const confirm = useConfirm();
  const [roles, setRoles] = useState<R[]>([]);
  const [permissions, setPermissions] = useState<PermissionDefinition[]>([]);
  const [targetItems, setTargetItems] = useState<Record<string, RolePermissionTargetItem[]>>({});
  const [targetLoadWarnings, setTargetLoadWarnings] = useState<Record<string, string>>({});
  const selection = useSecurityEditorSelection("role");
  const openedId = selection.selectedId;
  const [search, setSearch] = useState("");
  const [targetSearch, setTargetSearch] = useState<Record<string, string>>({});
  const [sort, setSort] = useState<DataTableSort>({ key: "role", direction: "asc" });
  const [draft, setDraft] = useState<RolePermissionsDraft>(EMPTY_DRAFT);
  const [baseline, setBaseline] = useState<RolePermissionsDraft>(EMPTY_DRAFT);
  // 今の draft がどのロールのものか。URL の対象が変わったら入れ直す。
  const [draftFor, setDraftFor] = useState<string | null>(null);
  const [lastOpenedId, setLastOpenedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [formError, setFormError] = useState("");
  const formRef = useRef<HTMLFormElement | null>(null);
  const loadSequence = useRef(0);
  const { abortAll, run: runScopedRequest } = useRequestScope();

  const editingRole = openedId ? (roles.find((role) => role.role_id === openedId) ?? null) : null;
  const view: PermissionView = !openedId ? "list" : editingRole ? "edit" : "missing";
  const draftKey = view === "edit" ? (editingRole?.role_id ?? null) : null;
  const draftFromRole = (role: R): RolePermissionsDraft => ({
    permissions: role.permissions,
    targets: Object.fromEntries(targets.map((item) => [item.key, item.selectedIds(role)])),
  });
  // URL の対象（別のロール・一覧）が変わったら、フォームをそのロールの値で入れ直す（render 中に同期する）。
  if (draftKey !== draftFor) {
    const nextDraft = editingRole && view === "edit" ? draftFromRole(editingRole) : EMPTY_DRAFT;
    setDraftFor(draftKey);
    setDraft(nextDraft);
    setBaseline(nextDraft);
    setTargetSearch({});
    setFormError("");
  }
  if (openedId && openedId !== lastOpenedId) setLastOpenedId(openedId);

  const readOnly = Boolean(!canManage || (editingRole && !permissionsEditable(editingRole)));
  const operationBusy = busy || loading;
  // 保存を試みた回数（保存の失敗の Banner を、同じ文言の失敗でも入れ直す。#585）。
  const [submitAttempt, setSubmitAttempt] = useState(0);
  const inputReadOnly = readOnly || operationBusy;
  const isDirty = view === "edit" && canonicalDraft(draft) !== canonicalDraft(baseline);
  const confirmLeave = async () =>
    !operationBusy &&
    (!isDirty ||
      (await confirm({
        title: m.discardTitle,
        description: m.discardDescription,
        confirmLabel: m.discardConfirm,
        tone: "danger",
        dismissOnOverlay: false,
      })));
  useUnsavedChangesGuard(isDirty || busy, confirmLeave);
  // 破棄して一覧へ戻るとき、離脱ガードが外れた commit の後で履歴を戻す（確認を二重に出さない）。
  const scheduleLeave = useFocusAfterCommit(!isDirty);
  const permissionByCode = useMemo(
    () => new Map(permissions.map((permission) => [permission.code, permission])),
    [permissions],
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
    [draft.permissions, permissionByCode],
  );
  const draftEffectivePermissionCodes = useMemo(
    () => effectivePermissionCodes(draft.permissions, permissionByCode),
    [draft.permissions, permissionByCode],
  );
  const draftGrantsAll = (target: RolePermissionTargetSection<R>) =>
    targetGrantsAll(target, editingRole?.role_code, draftEffectivePermissionCodes);

  /** ロールの利用できる対象の件数（全件が対象なら null）。一覧の列と狭い画面の補足に使う。 */
  const targetSummaries = useCallback(
    (role: R) => {
      const effectiveCodes = effectivePermissionCodes(role.permissions, permissionByCode);
      return targets.map((target) => {
        const grantsAll = targetGrantsAll(target, role.role_code, effectiveCodes);
        const selected = target.selectedIds(role);
        const items = targetItemsFor(target, targetItems[target.key] ?? [], selected).filter((item) =>
          selected.includes(item.id),
        );
        return { target, grantsAll, items };
      });
    },
    [permissionByCode, targetItems, targets],
  );
  // 狭い画面の補足（「対象の名前: N 件」「対象の名前: すべて」で書き方をそろえる）。
  const targetSummaryText = (summary: ReturnType<typeof targetSummaries>[number]) =>
    `${summary.target.messages.title}: ${
      summary.grantsAll
        ? m.allTargets
        : formatMessage(summary.target.messages.count ?? m.permissionCount, { count: summary.items.length })
    }`;
  const permissionCountText = (role: R) =>
    role.role_code === SYSTEM_ADMIN_ROLE_CODE
      ? m.allPermissions
      : formatMessage(m.permissionCountOfTotal, {
          count: effectivePermissionCodes(role.permissions, permissionByCode).size,
          total: permissions.length,
        });

  // 一覧の検索（useMemo）から使うため、権限定義・対象の候補が変わったときだけ作り直す。
  const roleSearchText = useCallback(
    (role: R) => {
      const effectiveCodes = effectivePermissionCodes(role.permissions, permissionByCode);
      return [
        role.role_code,
        role.display_name,
        ...[...effectiveCodes].map((code) => permissionByCode.get(code)?.label ?? code),
        ...targetSummaries(role).map(({ target, grantsAll, items }) =>
          grantsAll ? target.messages.all : items.map(targetItemLabel).join(" "),
        ),
      ]
        .join(" ")
        .toLowerCase();
    },
    [permissionByCode, targetSummaries],
  );

  const filteredRoles = useMemo(() => {
    const q = search.trim().toLowerCase();
    return roles
      .filter((role) => (q ? roleSearchText(role).includes(q) : true))
      .sort((left, right) => {
        if (sort.key === "permissions") {
          return compareNumber(
            effectivePermissionCodes(left.permissions, permissionByCode).size,
            effectivePermissionCodes(right.permissions, permissionByCode).size,
            sort.direction,
          );
        }
        // 1 列目はロール名を出すため、ロール名で並べる（#1682）。
        return compareText(roleDisplayName(left), roleDisplayName(right), sort.direction);
      });
  }, [permissionByCode, roleSearchText, roles, search, sort]);

  // 一覧は 25 件ずつのページ（表示件数は選べる）。検索語・並べ替えが変わったら 1 ページ目へ戻す（#1891）。
  const roleList = usePagedSelectionList({
    items: filteredRoles,
    getKey: (role) => role.role_id,
    selectedId: lastOpenedId,
    active: view === "list",
    ready: !loading,
    resetKey: `${search}\u0000${sort.key}\u0000${sort.direction}`,
    pagingKey: "permissionRoles",
  });
  const roleListCount = securityListCount(roles.length, filteredRoles.length, search);
  useReturnFocusToRow(view === "list", lastOpenedId);

  const load = async (announce = false) => {
    if (busy) return;
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    setLoading(true);
    setLoadError("");
    setTargetLoadWarnings({});
    await requestData(sequence, announce);
  };

  // 読込中・エラー表示の初期化は呼び出し側で行う（初回表示は初期 state が読込中）。
  const requestData = async (sequence: number, announce: boolean) => {
    try {
      await runScopedRequest(async (signal) => {
        const [roleRows, permissionRows] = await Promise.all([
          api.roles(true, { signal }),
          api.permissions({ signal }),
        ]);
        if (signal.aborted || sequence !== loadSequence.current) return;
        // 候補は全件を読まない（#608）。一覧の検索とエディタに要る「ロールに選択済みの対象」の名前だけを `ids` で読む。
        // 取得の失敗は警告にとどめ、ロール一覧と機能権限は表示する（名前は ID のまま）。
        const targetRows = await Promise.all(
          targets.map((target) =>
            resolveTargetItems(
              target,
              roleRows.flatMap((role) => target.selectedIds(normalizedRole(role))),
              signal,
            )
              .then(({ items, warning }) => ({ key: target.key, rows: items, warning }))
              .catch((cause: unknown) => {
                if (isAbortError(cause)) throw cause;
                const message = apiErrorMessage(cause, m.loadError);
                return {
                  key: target.key,
                  rows: [] as RolePermissionTargetItem[],
                  warning: formatMessage(target.messages.loadWarning, { message }),
                };
              }),
          ),
        );
        if (signal.aborted || sequence !== loadSequence.current) return;
        setRoles(roleRows.map(normalizedRole));
        setPermissions(permissionRows);
        setTargetItems(Object.fromEntries(targetRows.map((row) => [row.key, row.rows])));
        setTargetLoadWarnings(
          Object.fromEntries(targetRows.filter((row) => row.warning).map((row) => [row.key, row.warning])),
        );
      });
      if (announce && sequence === loadSequence.current) {
        toast.success(m.refreshed);
      }
    } catch (cause) {
      if (isAbortError(cause)) return;
      const nextError = apiErrorMessage(cause, m.loadError);
      if (sequence === loadSequence.current) setLoadError(nextError);
    } finally {
      if (sequence === loadSequence.current) setLoading(false);
    }
  };

  // 初回ロードは mount 時だけ行う。最新の requestData を commit 時に ref へ入れて呼ぶ。
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

  const openRole = (role: R) => {
    if (operationBusy) return;
    selection.open(role.role_id);
  };

  const returnToList = async () => {
    if (!(await confirmLeave())) return;
    if (isDirty) {
      setDraft(baseline);
      scheduleLeave(selection.close);
      return;
    }
    selection.close();
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (inputReadOnly || !editingRole) return;
    setSubmitAttempt((current) => current + 1);
    setBusy(true);
    setFormError("");
    try {
      // 全件が対象のときは個別の ID を保存しない（空の一覧を送る）。
      const updated = normalizedRole(
        await api.save(editingRole, {
          permissions: draft.permissions,
          targets: Object.fromEntries(
            targets.map((target) => [
              target.key,
              draftGrantsAll(target) ? [] : (draft.targets[target.key] ?? []),
            ]),
          ),
        }),
      );
      setRoles((rows) => rows.map((row) => (row.role_id === updated.role_id ? updated : row)));
      // 保存した値を基準にしてエディタに残る。
      const nextDraft = draftFromRole(updated);
      setDraft(nextDraft);
      setBaseline(nextDraft);
      toast.success(m.saved);
    } catch (cause) {
      setFormError(apiErrorMessage(cause, m.saveError));
    } finally {
      setBusy(false);
    }
  };

  const togglePermission = (code: string) => {
    if (inputReadOnly) return;
    setDraft((current) => ({
      ...current,
      permissions: current.permissions.includes(code)
        ? current.permissions.filter((value) => value !== code)
        : [...current.permissions, code],
    }));
  };
  const permissionCodes = permissions.map((permission) => permission.code);
  const selectedPermissionCount = permissionCodes.filter((code) => draft.permissions.includes(code)).length;
  const selectPermissions = (codes: string[]) => {
    if (inputReadOnly) return;
    setDraft((current) => ({
      ...current,
      permissions: [...new Set([...current.permissions, ...codes])],
    }));
  };
  const clearPermissions = (codes: string[]) => {
    if (inputReadOnly) return;
    const codeSet = new Set(codes);
    setDraft((current) => ({
      ...current,
      permissions: current.permissions.filter((code) => !codeSet.has(code)),
    }));
  };
  const setTargetIds = (key: string, update: (ids: string[]) => string[]) =>
    setDraft((current) => ({
      ...current,
      targets: { ...current.targets, [key]: update(current.targets[key] ?? []) },
    }));
  // エディタで読んだ候補の名前を残す（保存後の一覧の検索・件数で、新しく選んだ対象の名前を使う。#608）。
  const rememberTargetItems = useCallback((key: string, items: readonly RolePermissionTargetItem[]) => {
    if (items.length === 0) return;
    setTargetItems((current) => ({ ...current, [key]: mergeTargetItems(current[key] ?? [], items) }));
  }, []);
  const reportTargetWarning = useCallback((key: string, warning: string) => {
    setTargetLoadWarnings((current) => (current[key] === warning ? current : { ...current, [key]: warning }));
  }, []);

  // 全幅の表（#4635）。md 未満は先頭の列だけを残し、機能権限と利用できる対象の件数は名前の下の補足に畳む。
  const roleColumns: Array<DataTableColumn<R>> = [
    {
      key: "role",
      header: m.columnRole,
      sortable: true,
      rowHeader: true,
      className: "align-top",
      render: (role) => (
        <RoleNameRowTitleButton
          securityRole={role}
          subtitle={
            <>
              <span className="block">{roleKindLabel(role)}</span>
              <span className="block md:hidden" data-testid="security-permissions-row-summary">
                {[permissionCountText(role), ...targetSummaries(role).map(targetSummaryText)].join(" · ")}
              </span>
            </>
          }
          href={selection.hrefFor(role.role_id)}
          data-security-row-id={role.role_id}
          aria-label={formatMessage(m.showRole, { name: roleDisplayName(role) })}
          onClick={(event) => {
            event.stopPropagation();
            openRole(role);
          }}
        />
      ),
    },
    {
      key: "permissions",
      header: m.permissions,
      sortable: true,
      headerClassName: "hidden w-[12rem] md:table-cell",
      className: "hidden align-top tabular-nums md:table-cell",
      render: (role) => permissionCountText(role),
    },
    // 利用できる対象は種類ごとの列（見出しは対象の名前、値は件数か「すべて」）。
    ...targets.map(
      (target, index) =>
        ({
          key: `target-${target.key}`,
          header: target.messages.title,
          headerClassName: "hidden md:table-cell",
          className: "hidden align-top tabular-nums md:table-cell",
          render: (role: R) => {
            const summary = targetSummaries(role)[index];
            return (
              <span data-testid="security-permissions-target-summary">
                {summary.grantsAll
                  ? m.allTargets
                  : formatMessage(target.messages.count ?? m.permissionCount, { count: summary.items.length })}
              </span>
            );
          },
        }) satisfies DataTableColumn<R>,
    ),
  ];

  const back = {
    label: m.backToList,
    onClick: () => void returnToList(),
    disabled: operationBusy,
    testId: "security-permissions-back",
  };
  const banners = (
    <>
      {loadError ? <Banner severity="danger">{loadError}</Banner> : null}
      {targets.map((target) =>
        targetLoadWarnings[target.key] ? (
          <Banner key={target.key} severity="warning">
            {targetLoadWarnings[target.key]}
          </Banner>
        ) : null,
      )}
    </>
  );

  if (view === "missing") {
    return (
      <>
        <PageHeader wide title={m.title} back={back} />
        <PageBody wide className="grid gap-4">
          {loadError ? (
            <Banner severity="danger">{loadError}</Banner>
          ) : (
            <SecurityEditorFallback
              loading={loading}
              loadingLabel={m.loading}
              title={m.notFoundTitle}
              hint={m.notFoundHint}
              onBack={() => selection.close()}
              testId="security-permissions-detail-not-found"
            />
          )}
        </PageBody>
      </>
    );
  }

  if (view === "edit" && editingRole) {
    const systemAdmin = editingRole.role_code === SYSTEM_ADMIN_ROLE_CODE;
    return (
      <>
        <PageHeader
          wide
          title={roleDisplayName(editingRole)}
          subtitle={m.formHint}
          status={<RoleStatusBadges role={editingRole} />}
          // 一覧へ戻るは左上（#618）。保存はフォームの末尾の FormActionBar（#1786）。
          back={back}
        />
        <PageBody wide className="grid grid-cols-1 gap-5">
          {banners}
          {systemAdmin ? (
            <Banner severity="info">{m.systemAdminNotice}</Banner>
          ) : editingRole.archived ? (
            <Banner severity="warning">{m.archivedPermissionNotice}</Banner>
          ) : editingRole.is_built_in ? (
            <Banner severity="info">{m.builtInReadonly}</Banner>
          ) : null}
          <form
            ref={formRef}
            className="grid gap-5"
            onSubmit={handleSubmit}
            aria-label={formatMessage(m.formTitle, { role: roleDisplayName(editingRole) })}
            data-testid="security-permissions-editor"
          >
            <Card>
              <CardHeader className="flex-row flex-wrap items-start justify-between gap-3">
                <div className="grid gap-1">
                  <CardTitle id="security-permissions-heading" className="flex flex-wrap items-center gap-2">
                    <LockKeyhole size={20} aria-hidden="true" />
                    {m.permissions}
                    <span className="text-sm font-normal tabular-nums text-fg-muted" data-testid="security-permissions-selected-count">
                      {systemAdmin
                        ? m.allPermissions
                        : formatMessage(m.permissionCountOfTotal, {
                            count: draftEffectivePermissionCodes.size,
                            total: permissions.length,
                          })}
                    </span>
                  </CardTitle>
                  <p className="text-sm text-fg-muted">{m.permissionsHint}</p>
                </div>
                {roleManagementPath ? (
                  <ButtonLink
                    to={`${roleManagementPath}?role=${encodeURIComponent(editingRole.role_id)}`}
                    linkComponent={Link}
                    size="sm"
                    icon={ShieldCheck}
                    testId="security-permissions-open-role"
                  >
                    {m.openRoleManagement}
                  </ButtonLink>
                ) : null}
              </CardHeader>
              <CardContent>
                <fieldset className="grid gap-3" disabled={inputReadOnly} aria-labelledby="security-permissions-heading">
                  {permissions.length > 0 && !readOnly ? (
                    <BulkSelectionActions
                      selectLabel={m.selectAll}
                      clearLabel={m.clearAll}
                      selectDisabled={readOnly || selectedPermissionCount === permissionCodes.length}
                      clearDisabled={readOnly || selectedPermissionCount === 0}
                      dataTestId="security-roles-permission-selection-actions"
                      onSelectAll={() => selectPermissions(permissionCodes)}
                      onClearAll={() => clearPermissions(permissionCodes)}
                    />
                  ) : null}
                  {permissionGroups.length === 0 ? (
                    <p className="rounded-md border border-dashed border-border p-4 text-sm text-fg-muted">
                      {m.empty}
                    </p>
                  ) : (
                    <div className="grid gap-3 lg:grid-cols-2">
                      {permissionGroups.map(([group, groupPermissions]) => {
                        const groupCodes = groupPermissions.map((permission) => permission.code);
                        const selectedGroupCount = groupCodes.filter((code) =>
                          draftEffectivePermissionCodes.has(code),
                        ).length;
                        return (
                          <div key={group} className="rounded-md border border-border p-3">
                            <div className="mb-2 grid gap-2">
                              <div className="flex flex-wrap items-baseline justify-between gap-2">
                                <h3 className="text-sm font-semibold">{group}</h3>
                                {/* 群の中の有効な権限の件数（継承を含む）。見出しの名前には含めない。 */}
                                <span className="text-xs tabular-nums text-fg-muted">
                                  {formatMessage(m.permissionCountOfTotal, {
                                    count: selectedGroupCount,
                                    total: groupCodes.length,
                                  })}
                                </span>
                              </div>
                              {!readOnly ? (
                                <BulkSelectionActions
                                  selectLabel={m.selectAll}
                                  clearLabel={m.clearAll}
                                  selectAriaLabel={formatMessage(m.selectGroup, { name: group })}
                                  clearAriaLabel={formatMessage(m.clearGroup, { name: group })}
                                  selectDisabled={readOnly || groupCodes.every((code) => draft.permissions.includes(code))}
                                  clearDisabled={readOnly || !groupCodes.some((code) => draft.permissions.includes(code))}
                                  dataTestId={`security-roles-${group}-permission-selection-actions`}
                                  onSelectAll={() => selectPermissions(groupCodes)}
                                  onClearAll={() => clearPermissions(groupCodes)}
                                />
                              ) : null}
                            </div>
                            <div className="grid gap-2">
                              {groupPermissions.map((permission) => {
                                const checkedDirect = draft.permissions.includes(permission.code);
                                const inheritedSources = draftInheritedPermissionSources.get(permission.code) ?? [];
                                const inherited = !checkedDirect && inheritedSources.length > 0;
                                return (
                                  <label
                                    key={permission.code}
                                    className={`flex min-h-11 items-start gap-2 text-sm ${
                                      readOnly || inherited ? "cursor-not-allowed opacity-80" : "cursor-pointer"
                                    }`}
                                  >
                                    <input
                                      className="mt-0.5 h-4 w-4 accent-accent-emphasis disabled:cursor-not-allowed"
                                      type="checkbox"
                                      checked={systemAdmin || checkedDirect || inherited}
                                      disabled={inputReadOnly || inherited}
                                      onChange={() => {
                                        if (!inherited) togglePermission(permission.code);
                                      }}
                                    />
                                    <span className="min-w-0">
                                      <span className="flex flex-wrap items-center gap-1.5 font-medium">
                                        <span>{permission.label}</span>
                                        {inherited ? (
                                          <StatusBadge
                                            icon={false}
                                            variant="neutral"
                                            label={formatMessage(m.permissionInherited, {
                                              source: inheritedSources[0],
                                            })}
                                          />
                                        ) : null}
                                      </span>
                                      <span className="block text-xs leading-5 text-fg-muted">
                                        {permission.description}
                                      </span>
                                      <code className="block break-all font-mono text-xs text-fg-muted">{permission.code}</code>
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
              </CardContent>
            </Card>

            {targets.map((target) => (
              <Card key={target.key} data-testid={`security-permissions-target-${target.key}`}>
                <CardContent className="pt-4">
                  <TargetFieldset
                    target={target}
                    knownItems={targetItems[target.key] ?? []}
                    selectedIds={draft.targets[target.key] ?? []}
                    search={targetSearch[target.key] ?? ""}
                    grantsAll={draftGrantsAll(target)}
                    systemAdmin={systemAdmin}
                    inputReadOnly={inputReadOnly}
                    messages={m}
                    onSearchChange={(value) => setTargetSearch((current) => ({ ...current, [target.key]: value }))}
                    onChange={(update) => setTargetIds(target.key, update)}
                    onItemsLoaded={rememberTargetItems}
                    onWarning={reportTargetWarning}
                  />
                </CardContent>
              </Card>
            ))}

            {/* 保存の失敗は確定の行の直上の 1 か所だけ（messaging.md §3.3.1。#585 / #1786）。 */}
            <SaveErrorBanner message={formError} attemptKey={submitAttempt} testId="security-permissions-save-error" />
            {!readOnly ? (
              // 確定はフォームの末尾の左（保存 → 変更を破棄）。画面の下端に貼り付けない（#1786 / #2715）。
              <FormActionBar
                ariaLabel={m.formActionsLabel}
                testId="security-permissions-form-actions"
                primaryActions={[
                  {
                    id: "save",
                    label: m.save,
                    icon: Save,
                    type: "submit",
                    loading: busy,
                    disabled: operationBusy && !busy,
                    testId: "security-permissions-submit",
                  },
                ]}
                secondaryActions={[
                  {
                    id: "discard",
                    label: m.discardChanges,
                    icon: RotateCcw,
                    disabled: !isDirty || operationBusy,
                    testId: "security-permissions-discard",
                    onClick: () => {
                      setDraft(baseline);
                      setFormError("");
                    },
                  },
                ]}
              />
            ) : null}
          </form>
        </PageBody>
      </>
    );
  }

  return (
    <>
      <PageHeader
        wide
        title={m.title}
        subtitle={m.subtitle}
        actions={[
          {
            id: "refresh",
            kind: "utility",
            label: m.refresh,
            icon: RefreshCw,
            disabled: operationBusy,
            onClick: () => load(true),
            loading,
          },
        ]}
        actionsLabel={m.actionsLabel}
        actionsTestId="security-permissions-actions"
      />
      <PageBody wide className="grid grid-cols-1 gap-4">
        {banners}
        <section className="grid min-w-0 gap-3" aria-label={m.listTitle} data-testid="security-permissions-list">
          {/* 件数はツールバーの右の ListCount 1 か所（#1891）。件数の読み上げは検索欄の resultCountLabel が行う。 */}
          <ListToolbar
            testId="security-permissions-toolbar"
            search={
              <SecuritySearchField
                label={m.search}
                placeholder={m.searchPlaceholder}
                value={search}
                testId="security-permissions-search"
                disabled={operationBusy}
                resultCountLabel={listCountText(roleListCount)}
                onChange={setSearch}
              />
            }
            count={{ ...roleListCount, live: false, testId: "security-permissions-count" }}
          />
          {loading ? (
            <ProcessingIndicator
              active
              label={m.loading}
              operationKey="security-permissions-load"
              placement="panel"
              testId="security-permissions-loading"
              activityIcon="none"
            />
          ) : null}
          <div className="grid gap-2">
            <DataTable
              loading={loading}
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
                "data-testid": `security-permissions-row-${role.role_id}`,
              })}
              ariaLabel={m.listTitle}
              testId="security-permissions-grid"
              scrollAriaLabel={formatMessage(m.listScrollLabel, { list: m.listTitle })}
              scrollTestId="security-permissions-scroll-region"
              tableClassName="w-full"
              stickyHeader
              visibleRows={INFORMATION_TABLE_VISIBLE_ROWS}
              growToViewport
              empty={
                // 読み取りに失敗したときは 0 件と言わない（原因は上の Banner。#4217）。
                loadError ? null : <EmptyState
                  title={search ? m.noResultsTitle : m.empty}
                  hint={search ? m.noResultsHint : undefined}
                  action={search ? <SecurityClearSearchAction onClear={() => setSearch("")} /> : undefined}
                />
              }
              columns={roleColumns}
            />
            <SecurityListPagination
              list={roleList}
              ariaLabel={m.pagination}
              testId="security-permissions-pagination"
              disabled={operationBusy}
            />
          </div>
        </section>
      </PageBody>
    </>
  );
}

/**
 * 利用できる対象 1 種類分の選択欄。全件が対象（SYSTEM_ADMIN・管理権限）のときは説明だけを出す。
 * 候補の一覧は、大量の候補から選ぶ共通の `ListPicker`（#600）: 左に検索、選択の行（表示中の一括選択・解除・
 * 「選択中だけ表示」）、名前と説明の行の listbox。候補はサーバー側で検索し、50 件ずつ「さらに読み込む」で足す（#608）。
 */
function TargetFieldset<R extends PermissionRole>({
  target,
  knownItems,
  selectedIds,
  search,
  grantsAll,
  systemAdmin,
  inputReadOnly,
  messages: m,
  onSearchChange,
  onChange,
  onItemsLoaded,
  onWarning,
}: {
  target: RolePermissionTargetSection<R>;
  knownItems: RolePermissionTargetItem[];
  selectedIds: string[];
  search: string;
  grantsAll: boolean;
  systemAdmin: boolean;
  inputReadOnly: boolean;
  messages: RolePermissionsMessages;
  onSearchChange: (value: string) => void;
  onChange: (update: (ids: string[]) => string[]) => void;
  onItemsLoaded: (key: string, items: readonly RolePermissionTargetItem[]) => void;
  onWarning: (key: string, warning: string) => void;
}) {
  const tm = target.messages;
  const idPrefix = `security-roles-${target.key}`;
  const targetReadOnly = inputReadOnly || grantsAll;
  const custom = target.allowCustomIds;
  // 直接入力で足した ID は、選択を外しても編集中は候補に残す（選び直せるように）。
  const [addedIds, setAddedIds] = useState<string[]>([]);

  return (
    <fieldset className="grid gap-3" disabled={targetReadOnly}>
      <legend id={`${idPrefix}-label`} className="text-base font-semibold">
        {tm.title}
      </legend>
      {tm.hint ? <p className="text-sm text-fg-muted">{tm.hint}</p> : null}
      {grantsAll ? (
        <Banner severity="info">{systemAdmin ? tm.grantsAllSystemAdmin : tm.grantsAllByPermission}</Banner>
      ) : (
        <>
          {custom ? (
            <CustomTargetIdField
              idPrefix={idPrefix}
              options={custom}
              disabled={targetReadOnly}
              onAdd={(id) => {
                setAddedIds((current) => (current.includes(id) ? current : [...current, id]));
                onChange((ids) => (ids.includes(id) ? ids : [...ids, id]));
                // 追加した ID が検索で隠れないよう、検索語を消す。
                if (search) onSearchChange("");
              }}
            />
          ) : null}
          <TargetPicker
            target={target}
            idPrefix={idPrefix}
            knownItems={knownItems}
            selectedIds={selectedIds}
            addedIds={addedIds}
            search={search}
            targetReadOnly={targetReadOnly}
            messages={m}
            onSearchChange={onSearchChange}
            onChange={onChange}
            onItemsLoaded={onItemsLoaded}
            onWarning={onWarning}
          />
        </>
      )}
    </fieldset>
  );
}

interface TargetPickerResult {
  items: RolePermissionTargetItem[];
  total: number;
  /** この結果の検索語（null はまだ 1 度も読めていない）。 */
  query: string | null;
}

/**
 * 候補の一覧（ListPicker）と、サーバー側の検索・追加読み込み（#608）。検索語が変わったら 1 ページ目から読み直し、
 * 前の候補を出したまま更新する。古い応答は連番と AbortController で捨てる（UX 契約「一覧の絞り込みの検索」6）。
 */
function TargetPicker<R extends PermissionRole>({
  target,
  idPrefix,
  knownItems,
  selectedIds,
  addedIds,
  search,
  targetReadOnly,
  messages: m,
  onSearchChange,
  onChange,
  onItemsLoaded,
  onWarning,
}: {
  target: RolePermissionTargetSection<R>;
  idPrefix: string;
  knownItems: RolePermissionTargetItem[];
  selectedIds: string[];
  addedIds: string[];
  search: string;
  targetReadOnly: boolean;
  messages: RolePermissionsMessages;
  onSearchChange: (value: string) => void;
  onChange: (update: (ids: string[]) => string[]) => void;
  onItemsLoaded: (key: string, items: readonly RolePermissionTargetItem[]) => void;
  onWarning: (key: string, warning: string) => void;
}) {
  const tm = target.messages;
  const custom = target.allowCustomIds;
  const [result, setResult] = useState<TargetPickerResult>({ items: [], total: 0, query: null });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState("");
  const sequence = useRef(0);
  const controller = useRef<AbortController | null>(null);

  const request = async (q: string, offset: number) => {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    sequence.current += 1;
    const requestSequence = sequence.current;
    const append = offset > 0;
    if (append) {
      setLoadingMore(true);
      setLoadMoreError("");
    } else {
      setLoading(true);
      setError("");
      setLoadingMore(false);
      setLoadMoreError("");
    }
    try {
      const page = await target.query({ q, limit: TARGET_PAGE_SIZE, offset }, { signal: current.signal });
      if (requestSequence !== sequence.current) return;
      setResult((previous) => ({
        items: append ? mergeTargetItems(previous.items, page.items) : page.items,
        total: page.total,
        query: q,
      }));
      onItemsLoaded(target.key, page.items);
      const warning = page.warning?.trim();
      if (warning) onWarning(target.key, warning);
    } catch (cause) {
      if (isAbortError(cause) || requestSequence !== sequence.current) return;
      const message = apiErrorMessage(cause, m.loadError);
      if (append) setLoadMoreError(message);
      else setError(message);
    } finally {
      if (requestSequence === sequence.current) {
        if (append) setLoadingMore(false);
        else setLoading(false);
      }
    }
  };

  // 検索語が変わったら 1 ページ目から読み直す。最新の request を commit 時に ref へ入れて呼ぶ。
  const requestRef = useRef(request);
  useLayoutEffect(() => {
    requestRef.current = request;
  });
  useEffect(() => {
    void requestRef.current(search, 0);
  }, [search]);
  useEffect(
    () => () => {
      sequence.current += 1;
      controller.current?.abort();
    },
    [],
  );

  const knownById = new Map<string, RolePermissionTargetItem>();
  for (const item of [...knownItems, ...result.items]) knownById.set(item.id, item);
  const itemFor = (id: string): RolePermissionTargetItem =>
    knownById.get(id) ?? (custom?.customStatus ? { id, name: id, status: custom.customStatus } : { id, name: id });
  // 直接入力で足した ID のうち、サーバーの候補に無いものを先頭に出す（検索語があれば画面側で絞る）。
  const loadedIds = new Set(result.items.map((item) => item.id));
  const q = search.trim().toLowerCase();
  const extraItems = custom
    ? addedIds
        .filter((id) => !loadedIds.has(id))
        .map(itemFor)
        .filter((item) => !q || [item.id, item.name].join(" ").toLowerCase().includes(q))
    : [];
  const visibleItems = [...extraItems, ...result.items];
  const selectedSet = new Set(selectedIds);
  const toPickerItem = (item: RolePermissionTargetItem): ListPickerItem => {
    const label = targetItemLabel(item);
    return {
      key: item.id,
      // 内部の ID は出さない（利用者には意味を持たない。#521）。名前が ID の候補（直接入力）は ID が名前になる。
      label,
      textValue: label,
      description: item.description?.trim() || undefined,
      meta: item.status ? <StatusBadge icon={false} variant="neutral" label={item.status} /> : undefined,
    };
  };
  const toggle = (id: string) => {
    if (targetReadOnly) return;
    onChange((ids) => (ids.includes(id) ? ids.filter((value) => value !== id) : [...ids, id]));
  };
  const selectVisible = () => {
    if (targetReadOnly) return;
    const visibleIds = visibleItems.map((item) => item.id);
    onChange((ids) => [...new Set([...ids, ...visibleIds])]);
  };
  const clearVisible = () => {
    if (targetReadOnly) return;
    const visible = new Set(visibleItems.map((item) => item.id));
    onChange((ids) => ids.filter((id) => !visible.has(id)));
  };
  const firstLoad = result.query === null;

  return (
    <ListPicker
      id={idPrefix}
      label={tm.title}
      items={visibleItems.map(toPickerItem)}
      selectedKeys={selectedSet}
      selectedItems={selectedIds.map((id) => toPickerItem(itemFor(id)))}
      onToggle={(item) => toggle(item.key)}
      onSelectMany={selectVisible}
      onClearSelection={clearVisible}
      total={result.total + extraItems.length}
      search={{
        id: `${idPrefix}-search`,
        label: tm.searchLabel,
        placeholder: tm.searchPlaceholder,
        value: search,
        disabled: targetReadOnly,
        onSearch: (value) => {
          if (targetReadOnly) return;
          onSearchChange(value);
        },
      }}
      loading={loading && firstLoad}
      refreshing={loading && !firstLoad}
      error={error || undefined}
      onRetry={() => void request(search, 0)}
      hasMore={!firstLoad && result.items.length < result.total}
      loadingMore={loadingMore}
      loadMoreError={loadMoreError || undefined}
      onLoadMore={() => void request(search, result.items.length)}
      disabled={targetReadOnly}
      labels={{
        resultCount: ({ visible, total, selected }) => securityFilteredCountWithSelected(visible, total, selected),
        emptyTitle: tm.empty,
        noResultsTitle: tm.noResults,
        noResultsHint: undefined,
        selectVisible: m.selectAll,
        clearSelection: m.clearAll,
      }}
      testId={`${idPrefix}-list`}
    />
  );
}

/**
 * 候補にない ID を直接入力して追加する欄（#215）。Enter でも追加し、フォームは送信しない。
 * 空・形式に合わない ID は追加せず、入力欄の直下に理由を出す。
 */
function CustomTargetIdField({
  idPrefix,
  options,
  disabled,
  onAdd,
}: {
  idPrefix: string;
  options: RolePermissionCustomIdOptions;
  disabled: boolean;
  onAdd: (id: string) => void;
}) {
  const [value, setValue] = useState("");
  const [error, setError] = useState("");
  const add = () => {
    if (disabled) return;
    const id = normalizeCustomTargetId(value, options.pattern);
    if (!id) {
      setError(options.invalidMessage);
      return;
    }
    onAdd(id);
    setValue("");
    setError("");
  };
  const hintId = `${idPrefix}-custom-id-hint`;
  const errorId = `${idPrefix}-custom-id-error`;
  const describedBy = [options.hint ? hintId : "", error ? errorId : ""].filter(Boolean).join(" ") || undefined;
  // ID の欄は md の段で、追加の操作は欄のすぐ右（FieldActionRow。#613 / #1891）。
  const rowWidth = fieldActionRowWidthClasses("md");
  return (
    <div className="rounded-md border border-border bg-surface-sunken p-3">
      <FieldActionRow
        className={rowWidth.row}
        fit={rowWidth.fit}
        actions={
          <Button
            type="button"
            variant="secondary"
            icon={Plus}
            disabled={disabled}
            onClick={add}
            data-testid={`${idPrefix}-custom-add`}
          >
            {options.addLabel}
          </Button>
        }
        footer={
          options.hint || error ? (
            <div className={cn("space-y-1.5", rowWidth.footer)}>
              {options.hint ? (
                <p id={hintId} className="text-xs leading-relaxed text-fg-muted">
                  {options.hint}
                </p>
              ) : null}
              <FieldError id={errorId} message={error} />
            </div>
          ) : null
        }
      >
        <TextField
          id={`${idPrefix}-custom-id`}
          width="md"
          label={options.label}
          placeholder={options.placeholder}
          value={value}
          disabled={disabled}
          autoComplete="off"
          spellCheck={false}
          data-testid={`${idPrefix}-custom-id`}
          // 補足とエラーは追加ボタンの行の下に出すため、入力欄との関連付けをここで持つ。
          aria-invalid={Boolean(error)}
          aria-describedby={describedBy}
          inputClassName={error ? "border-danger-fg" : undefined}
          onValueChange={(next) => {
            setValue(next);
            if (error) setError("");
          }}
          onKeyDown={(event) => {
            // IME の変換を確定する Enter では追加しない（Safari の keyCode 229 も含めて判定する。#535）。
            if (!isSubmitEnter(event)) return;
            event.preventDefault();
            add();
          }}
        />
      </FieldActionRow>
    </div>
  );
}
