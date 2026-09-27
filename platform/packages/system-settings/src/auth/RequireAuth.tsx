import type { ReactNode } from "react";
import { KeyRound } from "lucide-react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { TimedLoadingState, type SidebarFooterAction } from "@engchina/production-ready-ui";

import { useAuth } from "./AuthProvider";
import { AUTH_MESSAGES, formatMessage, type AuthMessages } from "./messages";
import type { AuthRoutes } from "./types";

export interface RequireAuthProps {
  routes: AuthRoutes;
  /** 今の URL を開くのに必要な権限コード（無ければ認証だけを確認する）。 */
  requiredPermission?: string;
  messages?: Partial<Pick<AuthMessages, "loading">>;
  children: ReactNode;
}

/**
 * 認証が必要な画面の入口（NL2SQL の App から移設。#220）。
 * 確認中は読み込み表示、未認証はログインへ（元の URL を state.from に載せる）、
 * 強制パスワード変更中はパスワード変更へ、権限が無ければ権限なしの画面へ移す。
 */
export function RequireAuth({ routes, requiredPermission, messages, children }: RequireAuthProps) {
  const auth = useAuth();
  const location = useLocation();
  const m = { ...AUTH_MESSAGES, ...messages };

  if (auth.status === "loading") {
    return (
      <main className="flex min-h-screen items-center justify-center bg-canvas p-4">
        <TimedLoadingState
          label={m.loading}
          operationKey="auth-session"
          placement="page"
          testId="auth-session-loading"
        />
      </main>
    );
  }
  if (auth.status === "unauthenticated") {
    return <Navigate to={routes.login} state={{ from: location.pathname }} replace />;
  }
  if (auth.user?.force_password_change) {
    return <Navigate to={routes.passwordChange} replace />;
  }
  if (requiredPermission && !auth.hasPermission(requiredPermission)) {
    return <Navigate to={routes.forbidden} replace />;
  }
  return <>{children}</>;
}

export interface SidebarAccount {
  name: string;
  roles: string;
  /** パスワード変更（許可されている場合だけ）。 */
  actions: SidebarFooterAction[];
  /** ローカル DEBUG（ログイン省略）では undefined。 */
  onLogout?: () => void;
  labels: { logout: string; switchToLight: string; switchToDark: string };
  /** ローカル DEBUG（ログイン省略）で入っているか。製品はその旨の notice を出す。 */
  debugMode: boolean;
}

/**
 * `SidebarAccountFooter` に渡す値（表示名・ロール・パスワード変更・ログアウト）を作る（#220）。
 * 未ログインなら null。ログアウト後はログイン画面へ移す。
 */
export function useSidebarAccount({
  routes,
  messages,
}: {
  routes: Pick<AuthRoutes, "login" | "passwordChange">;
  messages?: Partial<AuthMessages>;
}): SidebarAccount | null {
  const auth = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const m = { ...AUTH_MESSAGES, ...messages };
  const user = auth.user;
  if (!user) return null;
  const passwordChangeActive =
    pathname === routes.passwordChange || pathname.startsWith(`${routes.passwordChange}/`);
  return {
    name: user.display_name,
    roles: formatMessage(m.sidebarRoles, { roles: user.role_codes.join(", ") }),
    debugMode: user.debug_mode,
    labels: { logout: m.sidebarLogout, switchToLight: "", switchToDark: "" },
    actions:
      !user.debug_mode && user.password_change_allowed !== false
        ? [
            {
              id: "password-change",
              label: m.sidebarPassword,
              icon: KeyRound,
              active: passwordChangeActive,
              onClick: () => navigate(routes.passwordChange),
            },
          ]
        : [],
    onLogout: user.debug_mode
      ? undefined
      : () => void auth.logout().finally(() => navigate(routes.login, { replace: true })),
  };
}
