import type { ReactNode } from "react";
import { KeyRound, KeySquare } from "lucide-react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { ApiErrorState, TimedLoadingState, type SidebarFooterAction } from "@production-ready/ui";

import { useAuth } from "./AuthProvider";
import { AUTH_MESSAGES, formatMessage, type AuthMessages } from "./messages";
import type { AuthRoutes, BaseCurrentUser } from "./types";

export interface RequireAuthProps {
  routes: AuthRoutes;
  /** 今の URL を開くのに必要な権限コード（無ければ認証だけを確認する）。 */
  requiredPermission?: string;
  messages?: Partial<Pick<AuthMessages, "loading" | "sessionCheckError" | "sessionCheckErrorFallback">>;
  children: ReactNode;
}

/**
 * 認証が必要な画面の入口（NL2SQL の App から移設。#220）。
 * 確認中は読み込み表示、確認の失敗（5xx・通信断）は失敗と再試行（#1061）、未認証はログインへ（元の URL を state.from に載せる）、
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
  if (auth.status === "error") {
    // ログイン済みかどうかが分からないため、ログイン画面へ移さず、失敗と再試行を出す（#1061）。
    return (
      <main className="flex min-h-screen items-center justify-center bg-canvas p-4">
        <div className="grid w-full max-w-lg gap-3" data-testid="auth-session-error">
          <h1 className="text-base font-semibold text-fg">{m.sessionCheckError}</h1>
          <ApiErrorState
            error={auth.error}
            fallback={m.sessionCheckErrorFallback}
            onRetry={() => void auth.refresh()}
          />
        </div>
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

/** アカウント欄の操作が開く画面。`apiKeys` は自分の API キー（RAG / NL2SQL だけが渡す。#1805）。 */
export type SidebarAccountRoutes = Pick<AuthRoutes, "login" | "passwordChange"> & { apiKeys?: string };

export interface SidebarAccount {
  name: string;
  /** ロールの表示名（複数は区切りでつなぐ）。アカウント欄の 2 行目に出す。 */
  roles: string;
  /** ロールの行の全文（「ロール: …」）。title と読み上げに使う。 */
  rolesLabel: string;
  /** 自分の API キー（`routes.apiKeys` を渡したとき）とパスワード変更（許可されている場合だけ）。 */
  actions: SidebarFooterAction[];
  /** ローカル DEBUG（ログイン省略）では undefined。 */
  onLogout?: () => void;
  labels: { logout: string; switchToLight: string; switchToDark: string };
  /** ローカル DEBUG（ログイン省略）で入っているか。製品はその旨の notice を出す。 */
  debugMode: boolean;
}

/**
 * アカウント欄に出すロール。code ではなく表示名を区切りでつなぐ。表示名の無いロールだけ code（#1644）。
 */
export function sidebarRoleNames(
  user: Pick<BaseCurrentUser, "role_codes" | "role_display_names">,
  separator: string
): string {
  return user.role_codes
    .map((code, index) => user.role_display_names?.[index]?.trim() || code)
    .join(separator);
}

/**
 * アカウント欄の 1 行目。ログイン ID（code）を出す。ほかの画面は表示名を出す（#1696）。
 * ログインの無い local（debug）はログイン ID が固定の記号なので、表示名（「ローカル利用者」など）を出す。
 */
export function sidebarAccountName(
  user: Pick<BaseCurrentUser, "login_user_id" | "display_name" | "debug_mode">
): string {
  if (user.debug_mode) return user.display_name;
  return user.login_user_id.trim() || user.display_name;
}

/**
 * `SidebarAccountFooter` に渡す値（ログイン ID・ロール名・パスワード変更・ログアウト）を作る（#220・#1696）。
 * 未ログインなら null。ログアウト後はログイン画面へ移す。
 */
export function useSidebarAccount({
  routes,
  messages,
}: {
  routes: SidebarAccountRoutes;
  messages?: Partial<AuthMessages>;
}): SidebarAccount | null {
  const auth = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const m = { ...AUTH_MESSAGES, ...messages };
  const user = auth.user;
  if (!user) return null;
  const roles = sidebarRoleNames(user, m.sidebarRoleSeparator);
  const isActive = (path: string) => pathname === path || pathname.startsWith(`${path}/`);
  const passwordChangeActive = isActive(routes.passwordChange);
  // 自分の API キー（RAG / NL2SQL だけ。#1805）。ログインしている全員が使える（local でも、local でだけ
  // 使えるキーを発行できる）。
  const apiKeysRoute = routes.apiKeys;
  const apiKeysAction = apiKeysRoute
    ? [
        {
          id: "api-keys",
          label: m.sidebarApiKeys,
          icon: KeySquare,
          active: isActive(apiKeysRoute),
          onClick: () => navigate(apiKeysRoute),
        },
      ]
    : [];
  return {
    name: sidebarAccountName(user),
    roles,
    rolesLabel: formatMessage(m.sidebarRoles, { roles }),
    debugMode: user.debug_mode,
    labels: { logout: m.sidebarLogout, switchToLight: "", switchToDark: "" },
    actions: [
      ...apiKeysAction,
      ...(!user.debug_mode && user.password_change_allowed !== false
        ? [
            {
              id: "password-change",
              label: m.sidebarPassword,
              icon: KeyRound,
              active: passwordChangeActive,
              onClick: () => navigate(routes.passwordChange),
            },
          ]
        : []),
    ],
    onLogout: user.debug_mode
      ? undefined
      : () => void auth.logout().finally(() => navigate(routes.login, { replace: true })),
  };
}
