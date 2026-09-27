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

import { AUTH_UNAUTHORIZED_EVENT } from "./events";
import { createPermissionCheck as defaultPermissionCheck } from "./permissions";
import type { AuthApi, AuthStatus, BaseCurrentUser, HasPermission } from "./types";

export interface AuthContextValue<U extends BaseCurrentUser = BaseCurrentUser> {
  status: AuthStatus;
  user: U | null;
  hasPermission: HasPermission;
  /** ログイン直後など、context にまだ反映されていないユーザーの権限を判定する。 */
  permissionCheckFor: (user: U | null) => HasPermission;
  login: (loginUserId: string, password: string) => Promise<U>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
  changePassword: (currentPassword: string, newPassword: string) => Promise<unknown>;
}

// 型引数ごとに context を分けられないため、内部では基底型で持ち useAuth<U>() で絞る。
const AuthContext = createContext<AuthContextValue<BaseCurrentUser> | null>(null);

/** 利用者と権限が同じかを判断する既定の key（user_uuid + 並べ替えた permissions）。 */
export function defaultIdentityKey(user: BaseCurrentUser): string {
  return JSON.stringify([user.user_uuid, [...user.permissions].sort()]);
}

function isAbortError(cause: unknown): boolean {
  return cause instanceof Error && cause.name === "AbortError";
}

export interface AuthProviderProps<U extends BaseCurrentUser> {
  api: AuthApi<U>;
  /**
   * 利用者や権限が変わったと判断する key。変わったら React Query の cache を cancel / clear する。
   * 既定は user_uuid + permissions。製品固有の認可項目（例: 業務プロファイルの利用権限）があれば足す。
   */
  identityKey?: (user: U) => string;
  /**
   * 認証状態を反映するたびに呼ぶ（ログイン中は user、未認証は null）。
   * 製品の下書きの持ち主の記録・消去などに使う。同じ値で繰り返し呼ばれるので冪等にする。
   */
  onIdentityChange?: (user: U | null) => void;
  /**
   * 権限の判定を作る。既定は SYSTEM_ADMIN なら true、それ以外は `permissions` に含まれるか。
   * 旧コードの読み替えなど、製品固有の展開が必要な場合だけ差し替える。
   */
  createPermissionCheck?: (user: U) => HasPermission;
  children: ReactNode;
}

/**
 * Cookie セッションの認証状態を持つ（NL2SQL から移設。#220）。
 * 起動時に `me` で確認し、401 のイベント（`notifyAuthStatus`）で未認証に戻す。
 */
export function AuthProvider<U extends BaseCurrentUser>({
  api,
  identityKey = defaultIdentityKey,
  onIdentityChange,
  createPermissionCheck,
  children,
}: AuthProviderProps<U>) {
  const queryClient = useQueryClient();
  const identity = useRef("");
  // props の関数は render ごとに変わりうるため、最新値を ref で読む（effect の再実行を避ける）。
  const optionsRef = useRef({ api, identityKey, onIdentityChange, createPermissionCheck });
  useEffect(() => {
    optionsRef.current = { api, identityKey, onIdentityChange, createPermissionCheck };
  });

  const applyIdentity = useCallback(
    (current: U | null) => {
      const { identityKey: keyOf, onIdentityChange: notify } = optionsRef.current;
      const next = current ? keyOf(current) : "";
      if (identity.current !== next) {
        void queryClient.cancelQueries();
        queryClient.clear();
        identity.current = next;
      }
      try {
        notify?.(current);
      } catch {
        /* storage が無効などでも認証を妨げない */
      }
    },
    [queryClient],
  );
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [user, setUser] = useState<U | null>(null);

  // state は応答の callback の中だけで更新する（effect から同期的に setState しない）。
  const refresh = useCallback(
    (signal?: AbortSignal): Promise<void> =>
      optionsRef.current.api.me({ signal }).then(
        (current) => {
          if (signal?.aborted) return;
          applyIdentity(current);
          setUser(current);
          setStatus("authenticated");
        },
        (cause: unknown) => {
          if (isAbortError(cause)) return;
          applyIdentity(null);
          setUser(null);
          setStatus("unauthenticated");
        },
      ),
    [applyIdentity],
  );

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
    window.addEventListener(AUTH_UNAUTHORIZED_EVENT, handleUnauthorized);
    return () => window.removeEventListener(AUTH_UNAUTHORIZED_EVENT, handleUnauthorized);
  }, [applyIdentity]);

  const login = useCallback(
    async (loginUserId: string, password: string) => {
      const current = await optionsRef.current.api.login(loginUserId, password);
      applyIdentity(current);
      setUser(current);
      setStatus("authenticated");
      return current;
    },
    [applyIdentity],
  );

  const logout = useCallback(async () => {
    try {
      await optionsRef.current.api.logout();
    } finally {
      applyIdentity(null);
      setUser(null);
      setStatus("unauthenticated");
    }
  }, [applyIdentity]);

  const changePassword = useCallback(
    (currentPassword: string, newPassword: string) =>
      optionsRef.current.api.changePassword(currentPassword, newPassword),
    [],
  );

  const permissionCheckFor = useCallback(
    (target: U | null): HasPermission =>
      target
        ? (optionsRef.current.createPermissionCheck ?? defaultPermissionCheck)(target)
        : () => false,
    [],
  );

  const value = useMemo<AuthContextValue<U>>(
    () => ({
      status,
      user,
      login,
      logout,
      refresh: () => refresh(),
      changePassword,
      permissionCheckFor,
      hasPermission: permissionCheckFor(user),
    }),
    [changePassword, login, logout, permissionCheckFor, refresh, status, user],
  );

  return (
    <AuthContext.Provider value={value as unknown as AuthContextValue<BaseCurrentUser>}>
      {children}
    </AuthContext.Provider>
  );
}

/** `AuthProvider` の状態と操作。U は製品の CurrentUser 型。 */
export function useAuth<U extends BaseCurrentUser = BaseCurrentUser>(): AuthContextValue<U> {
  const value = useContext(AuthContext);
  if (!value) throw new Error("AuthProvider が設定されていません。");
  return value as unknown as AuthContextValue<U>;
}
