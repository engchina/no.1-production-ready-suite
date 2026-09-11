import { createContext, useContext, useEffect, useRef, useState, type Dispatch, type ReactNode, type SetStateAction } from "react";
import { useLocation } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { create } from "zustand";
import { Banner } from "@engchina/production-ready-ui";
import { useAuth } from "@/features/security/AuthProvider";
import { t } from "@/lib/i18n";
import { draftKey, readDraft, writeDraft } from "@/lib/workspace-drafts";

const WorkspaceContext = createContext("legacy");
const ActivityContext = createContext({ active: true, page: "" });
const useDraftFailures = create<{ keys: Set<string>; mark: (key: string, failed: boolean) => void }>((set) => ({
  keys: new Set(),
  mark: (key, failed) => set((state) => {
    if (state.keys.has(key) === failed) return state;
    const keys = new Set(state.keys);
    if (failed) keys.add(key); else keys.delete(key);
    return { keys };
  }),
}));

export function WorkspaceBoundary({ contextId, children }: { contextId: string; children: ReactNode }) {
  return <WorkspaceContext.Provider value={contextId}><div key={contextId}>{children}</div></WorkspaceContext.Provider>;
}
export function WorkspacePage({ active, page, children }: { active: boolean; page: string; children: ReactNode }) {
  return <ActivityContext.Provider value={{ active, page }}><div hidden={!active}>{children}</div></ActivityContext.Provider>;
}
export function useWorkspaceActive() { return useContext(ActivityContext).active; }

/** keep-alive 内でも離脱時に確認・破壊的選択だけを破棄する。 */
export function useResetExecutionConsent(reset: () => void, signature: string) {
  const active = useWorkspaceActive();
  const latest = useRef(reset);
  latest.current = reset;
  useEffect(() => { latest.current(); }, [active, signature]);
}

export function useWorkspaceState<T>(field: string, initial: T): [T, Dispatch<SetStateAction<T>>] {
  const { user } = useAuth();
  const context = useContext(WorkspaceContext);
  const activity = useContext(ActivityContext);
  const { pathname } = useLocation();
  const owner = user?.user_uuid ?? "";
  const key = draftKey(owner, context, activity.page || pathname, field);
  const mark = useDraftFailures((state) => state.mark);
  const read = () => { try { return readDraft(window.sessionStorage, key, initial); } catch { return initial; } };
  const [value, setValue] = useState<T>(read);
  const previousKey = useRef(key);
  if (previousKey.current !== key) {
    previousKey.current = key;
    setValue(read());
  }
  useEffect(() => {
    let saved = false;
    try { saved = writeDraft(window.sessionStorage, owner, key, value); } catch { /* storage disabled */ }
    mark(key, !saved);
    return () => mark(key, false);
  }, [key, mark, owner, value]);
  return [value, setValue];
}

export function WorkspaceDraftWarning() {
  const failed = useDraftFailures((state) => state.keys.size > 0);
  useEffect(() => {
    if (!failed) return;
    const protect = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [failed]);
  return failed ? <div className="px-4 pt-4"><Banner severity="warning">{t("workspace.storageFailed")}</Banner></div> : null;
}

/** 再表示時は stale な読み取り query のみ更新する。生成・実行 mutation は再送しない。 */
export function useWorkspaceRevalidation() {
  const active = useWorkspaceActive();
  const queryClient = useQueryClient();
  const visited = useRef(false);
  useEffect(() => {
    if (active && visited.current) void queryClient.refetchQueries({
      type: "active", stale: true,
      // job は既存 coordinator に任せ、完了済み job を復帰時に再ポーリングしない。
      predicate: (query) => query.queryKey[0] === "schema"
        ? ["catalog", "objects"].includes(String(query.queryKey[1]))
        : query.queryKey[0] === "nl2sql" && ["profiles", "db-admin"].includes(String(query.queryKey[1])),
    });
    if (active) visited.current = true;
  }, [active, queryClient]);
}

export function useWorkspaceActivation(onActivate: () => void) {
  const active = useWorkspaceActive();
  const wasActive = useRef(false);
  const callback = useRef(onActivate);
  callback.current = onActivate;
  useEffect(() => {
    if (active && !wasActive.current) callback.current();
    wasActive.current = active;
  }, [active]);
}

/** Profile の推薦を明示適用するときだけ、そのクエリを移動先の草稿へ引き継ぐ。 */
export function useWorkspaceDraftWriter() {
  const { user } = useAuth();
  const context = useContext(WorkspaceContext);
  const activity = useContext(ActivityContext);
  const { pathname } = useLocation();
  return (field: string, value: string) => {
    const owner = user?.user_uuid ?? "";
    try { return writeDraft(window.sessionStorage, owner, draftKey(owner, context, activity.page || pathname, field), value); } catch { return false; }
  };
}

export function WorkspaceResultNotice({ result, inputSignature, finishedAt, restored = false }: { result: object | null; inputSignature: string; finishedAt?: string | number | null; restored?: boolean }) {
  const active = useWorkspaceActive();
  const snapshot = useRef({ result, inputSignature, at: new Date().toISOString() });
  const [previous, setPrevious] = useState<object | null>(null);
  if (snapshot.current.result !== result) snapshot.current = { result, inputSignature, at: new Date().toISOString() };
  useEffect(() => { if (!active) setPrevious(result); }, [active, result]);
  if (!result) return null;
  const changed = snapshot.current.inputSignature !== inputSignature;
  if (!restored && previous !== result && !changed) return null;
  return <Banner severity="info" title={t("workspace.previousResult")}>
    {t("workspace.executedAt", { date: new Date(finishedAt || snapshot.current.at).toLocaleString("ja-JP") })}
    {changed ? ` — ${t("workspace.inputChanged")}` : ""}
  </Banner>;
}

/** ファイル内容など、一時保存の対象外である未保存入力を再読込から保護する。 */
export function useTransientDraftGuard(enabled: boolean) {
  useEffect(() => {
    if (!enabled) return;
    const protect = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [enabled]);
}
