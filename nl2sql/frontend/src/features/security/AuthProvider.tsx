import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { useQueryClient } from "@tanstack/react-query";
import { bindWorkspaceOwner, clearWorkspaceDrafts } from "@/lib/workspace-drafts";

import { isAbortError } from "@/lib/api";
import { securityApi } from "./api";
import { currentUserHasPermission, normalizeMenuPermissions } from "./menu-permissions";
import type { CurrentUser } from "./types";

type AuthStatus = "loading" | "authenticated" | "unauthenticated";

interface AuthContextValue {
  status: AuthStatus;
  user: CurrentUser | null;
  hasPermission: (permission: string) => boolean;
  login: (loginUserId: string, password: string) => Promise<CurrentUser>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const identity = useRef("");
  const applyIdentity = useCallback((current: CurrentUser | null) => {
    const next = current ? JSON.stringify([current.user_uuid, [...current.permissions].sort(), current.data_entitlements, current.allowed_profile_ids]) : "";
    if (identity.current !== next) {
      void queryClient.cancelQueries();
      queryClient.clear();
      identity.current = next;
    }
    try {
      if (current) bindWorkspaceOwner(window.sessionStorage, current.user_uuid);
      else clearWorkspaceDrafts(window.sessionStorage);
    } catch { /* storage が無効でも認証を妨げない */ }
  }, [queryClient]);
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [user, setUser] = useState<CurrentUser | null>(null);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const current = await securityApi.me({ signal });
      if (signal?.aborted) return;
      applyIdentity(current);
      setUser(current);
      setStatus("authenticated");
    } catch (cause) {
      if (isAbortError(cause)) return;
      applyIdentity(null);
      setUser(null);
      setStatus("unauthenticated");
    }
  }, [applyIdentity]);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  useEffect(() => {
    const handleUnauthorized = () => {
      applyIdentity(null);
      setUser(null);
      setStatus("unauthenticated");
    };
    window.addEventListener("app-auth-unauthorized", handleUnauthorized);
    return () => window.removeEventListener("app-auth-unauthorized", handleUnauthorized);
  }, [applyIdentity]);

  const login = useCallback(async (loginUserId: string, password: string) => {
    const current = await securityApi.login(loginUserId, password);
    applyIdentity(current);
    setUser(current);
    setStatus("authenticated");
    return current;
  }, [applyIdentity]);

  const logout = useCallback(async () => {
    try {
      await securityApi.logout();
    } finally {
      applyIdentity(null);
      setUser(null);
      setStatus("unauthenticated");
    }
  }, [applyIdentity]);

  const value = useMemo<AuthContextValue>(
    () => {
      const normalizedPermissions = user ? normalizeMenuPermissions(user.permissions) : new Set<string>();
      return {
        status,
        user,
        login,
        logout,
        refresh,
        hasPermission: (permission) => {
          return currentUserHasPermission(user, permission, normalizedPermissions);
        },
      };
    },
    [login, logout, refresh, status, user]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("AuthProvider が設定されていません。");
  return value;
}
