import { useCallback, useEffect, useLayoutEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";

/**
 * ページ遷移・再読込のときに残す作業状態（platform UX 契約 workspace-state.md。#87）。
 *
 * - 保存先は同じタブの `sessionStorage` だけ。ブラウザを閉じれば消え、恒久保存の代わりにしない。
 * - 保存してよい field は `WORKSPACE_FIELDS` の allowlist に明示登録したものだけ。
 *   秘密情報・資格情報・確認語・サーバー応答全体は登録しない。
 * - 各レコードは期限（`WORKSPACE_TTL_MS`）と文字数の上限を持つ。読めない・壊れた・期限切れの値は捨てて既定値に戻す。
 * - storage が使えない環境（プライベートモード等）でも画面は既定値で動く（読み書きはすべて try/catch）。
 */
export const WORKSPACE_NAMESPACE = "production-ready-agent.workspace.v1:";
export const WORKSPACE_TTL_MS = 8 * 60 * 60 * 1000;
const MAX_VALUE_CHARS = 20_000;

/**
 * 保存してよい field の allowlist（ページ → field）。
 * A 型（一覧 → 全画面エディタ）の編集対象は URL の `?id=` が唯一の情報源なので、ここには置かない（#137）。
 */
export const WORKSPACE_FIELDS = {
  runs: ["selectedRunId", "streamMode", "goal"],
  audit: ["filterForm", "appliedForm"],
  memory: ["query"],
} as const;

export type WorkspacePage = keyof typeof WORKSPACE_FIELDS;
export type WorkspaceField<P extends WorkspacePage> = (typeof WORKSPACE_FIELDS)[P][number];
export type WorkspaceValidator<T> = (value: unknown) => value is T;

interface StoredRecord {
  value: unknown;
  expiresAt: number;
}

function storageKey(page: WorkspacePage, field: string): string {
  return `${WORKSPACE_NAMESPACE}${page}.${field}`;
}

function isAllowed(page: WorkspacePage, field: string): boolean {
  return (WORKSPACE_FIELDS[page] as readonly string[]).includes(field);
}

function sameTypeAs<T>(initial: T): WorkspaceValidator<T> {
  return (value: unknown): value is T => typeof value === typeof initial && value !== null;
}

export const isString: WorkspaceValidator<string> = (value): value is string => typeof value === "string";

export const isNullableString: WorkspaceValidator<string | null> = (value): value is string | null =>
  value === null || typeof value === "string";

export function isOneOf<T extends string>(values: readonly T[]): WorkspaceValidator<T> {
  return (value: unknown): value is T => typeof value === "string" && (values as readonly string[]).includes(value);
}

/** 保存済みの値を読む。無い・壊れた・期限切れ・型違いのときは `initial` を返す。 */
export function readWorkspaceValue<P extends WorkspacePage, T>(
  page: P,
  field: WorkspaceField<P>,
  initial: T,
  isValid: WorkspaceValidator<T> = sameTypeAs(initial)
): T {
  if (!isAllowed(page, field)) return initial;
  try {
    const raw = window.sessionStorage.getItem(storageKey(page, field));
    if (raw === null) return initial;
    const record = JSON.parse(raw) as Partial<StoredRecord> | null;
    if (!record || typeof record.expiresAt !== "number" || record.expiresAt <= Date.now()) {
      window.sessionStorage.removeItem(storageKey(page, field));
      return initial;
    }
    return isValid(record.value) ? record.value : initial;
  } catch {
    return initial;
  }
}

/** 値を保存する。既定値と同じなら消す。保存できなかったら false を返す。 */
export function writeWorkspaceValue<P extends WorkspacePage>(
  page: P,
  field: WorkspaceField<P>,
  value: unknown,
  initial?: unknown
): boolean {
  if (!isAllowed(page, field)) return false;
  try {
    const key = storageKey(page, field);
    if (initial !== undefined && JSON.stringify(value) === JSON.stringify(initial)) {
      window.sessionStorage.removeItem(key);
      return true;
    }
    const raw = JSON.stringify({ value, expiresAt: Date.now() + WORKSPACE_TTL_MS } satisfies StoredRecord);
    if (raw.length > MAX_VALUE_CHARS) return false;
    window.sessionStorage.setItem(key, raw);
    return true;
  } catch {
    return false;
  }
}

/** この製品の作業状態をすべて消す（ログアウト・アカウント切替で呼ぶ）。 */
export function clearWorkspaceState(): void {
  try {
    const keys: string[] = [];
    for (let index = 0; index < window.sessionStorage.length; index += 1) {
      const key = window.sessionStorage.key(index);
      if (key?.startsWith(WORKSPACE_NAMESPACE)) keys.push(key);
    }
    keys.forEach((key) => window.sessionStorage.removeItem(key));
  } catch {
    // storage が使えない環境では消すものもない。
  }
}

/**
 * `useState` と同じ形で、allowlist の field を sessionStorage に残す。
 * 3 番目の戻り値は直近の保存に成功したか（失敗時は呼び出し側で離脱の保護を出す）。
 */
export function useWorkspaceState<P extends WorkspacePage, T>(
  page: P,
  field: WorkspaceField<P>,
  initial: T,
  isValid?: WorkspaceValidator<T>
): [T, Dispatch<SetStateAction<T>>, boolean] {
  const initialRef = useRef(initial);
  const [value, setValue] = useState<T>(() => readWorkspaceValue(page, field, initial, isValid));
  const [saved, setSaved] = useState(true);

  useEffect(() => {
    setSaved(writeWorkspaceValue(page, field, value, initialRef.current));
  }, [page, field, value]);

  return [value, setValue, saved];
}

/**
 * 復元した選択が今の一覧に存在するかを、一覧の取得後に 1 度だけ検証する。
 * 存在しなければ `onMissing` を呼び、利用者に説明するための `missing` を true にする
 * （選択を黙って別の対象に置き換えない）。
 */
export function useRestoredSelectionCheck(
  restoredId: string | null,
  availableIds: readonly string[] | undefined,
  onMissing?: () => void
): { missing: boolean; dismiss: () => void } {
  const pendingRef = useRef(restoredId);
  const onMissingRef = useRef(onMissing);
  // 最新の onMissing を commit 時に入れる（render 中に ref を書かない）。
  useLayoutEffect(() => {
    onMissingRef.current = onMissing;
  });
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    const pending = pendingRef.current;
    if (pending === null || availableIds === undefined) return;
    pendingRef.current = null;
    if (!availableIds.includes(pending)) {
      setMissing(true);
      onMissingRef.current?.();
    }
  }, [availableIds]);

  const dismiss = useCallback(() => setMissing(false), []);
  return { missing, dismiss };
}
