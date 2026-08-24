import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

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
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [user, setUser] = useState<CurrentUser | null>(null);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const current = await securityApi.me({ signal });
      if (signal?.aborted) return;
      setUser(current);
      setStatus("authenticated");
    } catch (cause) {
      if (isAbortError(cause)) return;
      setUser(null);
      setStatus("unauthenticated");
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  useEffect(() => {
    const handleUnauthorized = () => {
      setUser(null);
      setStatus("unauthenticated");
    };
    window.addEventListener("app-auth-unauthorized", handleUnauthorized);
    return () => window.removeEventListener("app-auth-unauthorized", handleUnauthorized);
  }, []);

  const login = useCallback(async (loginUserId: string, password: string) => {
    const current = await securityApi.login(loginUserId, password);
    setUser(current);
    setStatus("authenticated");
    return current;
  }, []);

  const logout = useCallback(async () => {
    try {
      await securityApi.logout();
    } finally {
      setUser(null);
      setStatus("unauthenticated");
    }
  }, []);

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
