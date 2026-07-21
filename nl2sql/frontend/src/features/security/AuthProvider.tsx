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
import type { CurrentUser } from "./types";

type AuthStatus = "loading" | "authenticated" | "unauthenticated";

interface AuthContextValue {
  status: AuthStatus;
  user: CurrentUser | null;
  hasPermission: (permission: string) => boolean;
  login: (loginName: string, password: string) => Promise<CurrentUser>;
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

  const login = useCallback(async (loginName: string, password: string) => {
    const current = await securityApi.login(loginName, password);
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
    () => ({
      status,
      user,
      login,
      logout,
      refresh,
      hasPermission: (permission) =>
        Boolean(
          user &&
            (user.role_codes.includes("SYSTEM_ADMIN") || user.permissions.includes(permission))
        ),
    }),
    [login, logout, refresh, status, user]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("AuthProvider が設定されていません。");
  return value;
}
