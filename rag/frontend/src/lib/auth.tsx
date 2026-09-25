"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, type ReactNode } from "react";

import { api, type AuthStatus, type AuthUser, type LoginRequestBody } from "./api";
import { queryKeys } from "./queries";

interface AuthContextValue {
  status: AuthStatus | null;
  user: AuthUser | null;
  authRequired: boolean;
  isAuthenticated: boolean;
  isLoading: boolean;
  isChecking: boolean;
  isLoggingIn: boolean;
  isLoggingOut: boolean;
  error: Error | null;
  login: (payload: LoginRequestBody) => Promise<AuthStatus>;
  logout: () => Promise<AuthStatus>;
  refetch: () => Promise<unknown>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/** 認証状態をアプリ全体で共有する。local mode ではログイン不要として扱う。 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: queryKeys.authStatus,
    queryFn: api.getAuthStatus,
    retry: false,
  });

  const loginMutation = useMutation({
    mutationFn: (payload: LoginRequestBody) => api.login(payload),
    onSuccess: (status) => {
      queryClient.setQueryData(queryKeys.authStatus, status);
    },
  });

  const logoutMutation = useMutation({
    mutationFn: api.logout,
    onSuccess: (status) => {
      queryClient.setQueryData(queryKeys.authStatus, status);
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
  });

  const status = query.data ?? null;
  const authRequired = status?.auth_required ?? true;
  const isAuthenticated = Boolean(status?.authenticated) || authRequired === false;

  return (
    <AuthContext.Provider
      value={{
        status,
        user: status?.user ?? null,
        authRequired,
        isAuthenticated,
        isLoading: query.isPending,
        isChecking: query.isFetching,
        isLoggingIn: loginMutation.isPending,
        isLoggingOut: logoutMutation.isPending,
        error: query.error instanceof Error ? query.error : null,
        login: loginMutation.mutateAsync,
        logout: logoutMutation.mutateAsync,
        refetch: query.refetch,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("useAuth は AuthProvider の内側で使用してください。");
  }
  return value;
}
