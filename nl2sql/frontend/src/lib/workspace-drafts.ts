/** 同一タブ専用の草稿。保存対象は各画面が明示登録した入力・表示条件・最小スナップショットだけ。 */
export const WORKSPACE_DRAFT_PREFIX = "production-ready-nl2sql.draft.v1:";
export const WORKSPACE_DRAFT_TTL_MS = 8 * 60 * 60 * 1000;
const OWNER_KEY = `${WORKSPACE_DRAFT_PREFIX}owner`;
export interface DraftStorage {
  length: number;
  key(index: number): string | null;
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}
export function clearWorkspaceDrafts(storage: DraftStorage): void {
  for (let index = storage.length - 1; index >= 0; index -= 1) {
    const key = storage.key(index);
    if (key?.startsWith(WORKSPACE_DRAFT_PREFIX)) storage.removeItem(key);
  }
}
export function bindWorkspaceOwner(storage: DraftStorage, owner: string): void {
  if (storage.getItem(OWNER_KEY) !== owner) clearWorkspaceDrafts(storage);
  storage.setItem(OWNER_KEY, owner);
}
export function draftKey(owner: string, context: string, page: string, field: string): string {
  return WORKSPACE_DRAFT_PREFIX + JSON.stringify([owner, context, page, field]);
}
function compatible(value: unknown, initial: unknown): boolean {
  if (initial === null) return value === null;
  if (Array.isArray(initial)) return Array.isArray(value) && value.every((item) => typeof item === "string");
  if (typeof initial === "object") {
    if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
    return Object.entries(initial).every(([key, sample]) => compatible((value as Record<string, unknown>)[key], sample));
  }
  return typeof value === typeof initial && (typeof value !== "number" || Number.isFinite(value));
}
export function readDraft<T>(storage: DraftStorage, key: string, initial: T, now = Date.now()): T {
  try {
    const raw = storage.getItem(key);
    if (!raw) return initial;
    const data = JSON.parse(raw);
    if (typeof data.at !== "number" || data.at > now || now - data.at >= WORKSPACE_DRAFT_TTL_MS || !compatible(data.value, initial)) {
      storage.removeItem(key);
      return initial;
    }
    return data.value as T;
  } catch {
    return initial;
  }
}
export function writeDraft(storage: DraftStorage, owner: string, key: string, value: unknown, now = Date.now()): boolean {
  try {
    // logout 後の非同期完了による草稿の復活も拒否する。
    if (storage.getItem(OWNER_KEY) !== owner) return false;
    const encoded = JSON.stringify({ at: now, value });
    if (encoded.length > 200_000) return false;
    storage.setItem(key, encoded);
    return true;
  } catch {
    return false;
  }
}
