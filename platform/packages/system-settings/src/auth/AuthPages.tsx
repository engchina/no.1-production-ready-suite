import { useState, type FormEvent, type ReactNode } from "react";
import { ArrowLeft, ArrowRight, KeyRound, LogIn, LogOut, ShieldCheck } from "lucide-react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import {
  Banner,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  ProcessingIndicator,
  TextField,
  toast,
} from "@engchina/production-ready-ui";

import { useAuth } from "./AuthProvider";
import { AUTH_MESSAGES, type AuthMessages } from "./messages";
import type { AuthRoutes, HasPermission } from "./types";

/** 認証画面の上に出す製品の wordmark（サイドバーの 2 行表記と同じもの）。 */
export interface AuthBrand {
  line1: string;
  line2: string;
}

export interface AuthPageProps {
  brand: AuthBrand;
  routes: Pick<AuthRoutes, "login" | "passwordChange">;
  /** ログイン後・戻る操作の既定の移動先。権限の判定を受け取って URL を返す。 */
  entryRoute: (hasPermission: HasPermission) => string;
  /** 既定の文言（日本語）の一部を上書きする。 */
  messages?: Partial<AuthMessages>;
}

function AuthSurface({ brand, children }: { brand: AuthBrand; children: ReactNode }) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-canvas px-4 py-10">
      <div className="w-full max-w-md space-y-5">
        <div className="flex items-center justify-center gap-3 text-center">
          <span className="inline-flex h-11 w-11 items-center justify-center rounded-xl bg-accent-emphasis text-fg-on-accent">
            <ShieldCheck size={24} aria-hidden />
          </span>
          <div className="text-left">
            <p className="text-sm font-semibold text-fg">{brand.line1}</p>
            <p className="text-xs text-fg-muted">{brand.line2}</p>
          </div>
        </div>
        {children}
      </div>
    </main>
  );
}

function errorText(cause: unknown, fallback: string): string {
  return cause instanceof Error && cause.message ? cause.message : fallback;
}

export interface LoginPageProps extends AuthPageProps {
  /**
   * ログイン失敗の表示文言。request ID などを含まない利用者向けの文だけを返す。
   * undefined を返すと Error.message、それも無ければ既定の文言を使う。
   */
  describeLoginError?: (cause: unknown) => string | undefined;
}

/** ログイン（NL2SQL から移設。#220）。ログイン済みなら既定の画面へ移す。 */
/**
 * ログイン前に開こうとしていた URL（`RequireAuth` が `state.from` に入れる）。
 * 別オリジンへ飛ばされないよう、`/` で始まるアプリ内のパスだけを使う。
 */
export function requestedPathFrom(state: unknown): string | null {
  const from = (state as { from?: unknown } | null)?.from;
  if (typeof from !== "string" || !from.startsWith("/") || from.startsWith("//") || from.includes("\\")) {
    return null;
  }
  return from;
}

export function LoginPage({ brand, routes, entryRoute, messages, describeLoginError }: LoginPageProps) {
  const m = { ...AUTH_MESSAGES, ...messages };
  const auth = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [loginUserId, setLoginUserId] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // ログインに成功すると、この再描画で移る。元の URL があればそこへ（権限は RequireAuth が確認する）。
  const requested = requestedPathFrom(location.state);
  if (auth.status === "authenticated") {
    return (
      <Navigate
        to={
          auth.user?.force_password_change
            ? routes.passwordChange
            : requested && requested !== routes.login
              ? requested
              : entryRoute(auth.hasPermission)
        }
        replace
      />
    );
  }

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!loginUserId.trim() || !password) {
      setError(m.loginRequired);
      return;
    }
    setBusy(true);
    setError("");
    try {
      const current = await auth.login(loginUserId, password);
      navigate(
        current.force_password_change
          ? routes.passwordChange
          : requested && requested !== routes.login
            ? requested
            : entryRoute(auth.permissionCheckFor(current)),
        { replace: true },
      );
    } catch (cause) {
      // 認証失敗は利用者の入力ミスなので、調査用のリクエスト ID は表示しない
      setError(describeLoginError?.(cause) || errorText(cause, m.loginError));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthSurface brand={brand}>
      <Card>
        <CardHeader>
          <CardTitle>{m.loginTitle}</CardTitle>
          <p className="text-sm leading-6 text-fg-muted">{m.loginSubtitle}</p>
        </CardHeader>
        <CardContent>
          <form className="space-y-4" onSubmit={handleSubmit} noValidate>
            {error ? <Banner severity="danger">{error}</Banner> : null}
            {/* ログインは画面の唯一の作業なので、入力欄と送信を lg（40px、タッチ端末は 44px）にそろえる（#613）。
                未入力は送信時に検証する（noValidate）。 */}
            <TextField
              id="auth-login-user-id"
              label={m.loginUserId}
              required
              requiredLabel={m.required}
              autoComplete="username"
              autoFocus
              size="lg"
              value={loginUserId}
              onValueChange={setLoginUserId}
            />
            <TextField
              id="auth-login-password"
              label={m.loginPassword}
              required
              requiredLabel={m.required}
              type="password"
              autoComplete="current-password"
              size="lg"
              value={password}
              onValueChange={setPassword}
            />
            <Button size="lg" className="w-full" loading={busy} type="submit" icon={LogIn}>
              {m.loginSubmit}
            </Button>
            {busy ? (
              <ProcessingIndicator
                active
                label={m.loginSubmit}
                operationKey="auth-login"
                placement="action"
                testId="auth-login-processing"
                activityIcon="none"
              />
            ) : null}
          </form>
        </CardContent>
      </Card>
    </AuthSurface>
  );
}

export interface PasswordChangePageProps extends AuthPageProps {
  /** 変更失敗の表示文言。undefined を返すと Error.message、それも無ければ既定の文言を使う。 */
  describeError?: (cause: unknown) => string | undefined;
}

const PASSWORD_FIELDS = [
  { id: "auth-password-current", label: "passwordCurrent", autoComplete: "current-password" },
  { id: "auth-password-new", label: "passwordNew", autoComplete: "new-password" },
  { id: "auth-password-confirm", label: "passwordConfirm", autoComplete: "new-password" },
] as const;

/**
 * パスワード変更（NL2SQL から移設。#220）。変更後はセッションを確認し直してログイン画面へ移す。
 * 強制変更中の「戻る」はログアウトしてログイン画面へ戻す。
 */
export function PasswordChangePage({ brand, routes, entryRoute, messages, describeError }: PasswordChangePageProps) {
  const m = { ...AUTH_MESSAGES, ...messages };
  const auth = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [values, setValues] = useState({
    "auth-password-current": "",
    "auth-password-new": "",
    "auth-password-confirm": "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  if (auth.status === "unauthenticated") return <Navigate to={routes.login} replace />;
  if (auth.user?.debug_mode) {
    return <Navigate to={entryRoute(auth.hasPermission)} replace />;
  }

  const fallbackRoute = entryRoute(auth.hasPermission);
  const canChangePassword = auth.user?.password_change_allowed !== false;
  const toLogin = () => navigate(routes.login, { replace: true });
  const handleBack = () => {
    if (window.history.length > 1 && location.key !== "default") {
      navigate(-1);
      return;
    }
    navigate(fallbackRoute, { replace: true });
  };
  const handleLeavePasswordChange = () => {
    if (auth.user?.force_password_change) {
      void auth.logout().finally(toLogin);
      return;
    }
    handleBack();
  };
  const handleLogout = () => {
    void auth.logout().finally(toLogin);
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const currentPassword = values["auth-password-current"];
    const newPassword = values["auth-password-new"];
    if (!currentPassword || !newPassword || !values["auth-password-confirm"]) {
      setError(m.passwordRequired);
      return;
    }
    if (newPassword !== values["auth-password-confirm"]) {
      setError(m.passwordMismatch);
      return;
    }
    setBusy(true);
    setError("");
    try {
      await auth.changePassword(currentPassword, newPassword);
      toast.success(m.passwordChanged);
      window.setTimeout(() => {
        void auth.refresh().finally(toLogin);
      }, 900);
    } catch (cause) {
      setError(describeError?.(cause) || errorText(cause, m.passwordSaveError));
    } finally {
      setBusy(false);
    }
  };

  if (!canChangePassword) {
    return (
      <AuthSurface brand={brand}>
        <Card>
          <CardHeader>
            <CardTitle>{m.passwordTitle}</CardTitle>
            <p className="text-sm leading-6 text-fg-muted">{m.passwordNotAllowedSubtitle}</p>
          </CardHeader>
          <CardContent className="space-y-5">
            <Banner severity="warning">{m.passwordNotAllowed}</Banner>
            <div className="grid gap-2 sm:grid-cols-2">
              <Button type="button" size="lg" className="w-full whitespace-nowrap" variant="secondary" onClick={handleBack} icon={ArrowLeft}>
                {m.passwordBack}
              </Button>
              <Button type="button" size="lg" className="w-full whitespace-nowrap" onClick={handleLogout} icon={LogOut}>
                {m.sidebarLogout}
              </Button>
            </div>
          </CardContent>
        </Card>
      </AuthSurface>
    );
  }

  return (
    <AuthSurface brand={brand}>
      <Card>
        <CardHeader>
          <CardTitle>{m.passwordTitle}</CardTitle>
          <p className="text-sm leading-6 text-fg-muted">{m.passwordSubtitle}</p>
        </CardHeader>
        <CardContent>
          <form className="space-y-4" onSubmit={handleSubmit} noValidate>
            {error ? <Banner severity="danger">{error}</Banner> : null}
            <Banner severity="info">{m.passwordRule}</Banner>
            {/* パスワードの変更も画面の唯一の作業なので lg（#613）。未入力は送信時に検証する（noValidate）。 */}
            {PASSWORD_FIELDS.map((field) => (
              <TextField
                key={field.id}
                id={field.id}
                label={m[field.label]}
                required
                requiredLabel={m.required}
                type="password"
                autoComplete={field.autoComplete}
                size="lg"
                value={values[field.id]}
                onValueChange={(value) => setValues((current) => ({ ...current, [field.id]: value }))}
              />
            ))}
            <div className="border-t border-border pt-4">
              <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.35fr)]">
                <Button
                  size="lg"
                  className="w-full whitespace-nowrap"
                  disabled={busy}
                  type="button"
                  variant="secondary"
                  onClick={handleLeavePasswordChange}
                  icon={ArrowLeft}
                >
                  {auth.user?.force_password_change ? m.passwordBackToLogin : m.passwordBack}
                </Button>
                <Button size="lg" className="w-full whitespace-nowrap" loading={busy} type="submit" icon={KeyRound}>
                  {m.passwordSubmit}
                </Button>
              </div>
              {busy ? (
                <ProcessingIndicator
                  active
                  label={m.passwordSubmit}
                  operationKey="auth-password-change"
                  placement="action"
                  className="mt-3"
                  testId="auth-password-processing"
                  activityIcon="none"
                />
              ) : null}
            </div>
          </form>
        </CardContent>
      </Card>
    </AuthSurface>
  );
}

export type ForbiddenPageProps = Pick<AuthPageProps, "brand" | "entryRoute" | "messages">;

/** 権限なし（NL2SQL から移設。#220）。403 の request ID を location.state から表示する。 */
export function ForbiddenPage({ brand, entryRoute, messages }: ForbiddenPageProps) {
  const m = { ...AUTH_MESSAGES, ...messages };
  const auth = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const requestId = (location.state as { requestId?: unknown } | null)?.requestId;
  return (
    <AuthSurface brand={brand}>
      <Card>
        <CardHeader>
          <CardTitle>{m.forbiddenTitle}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          <Banner severity="warning">
            <p>{m.forbiddenDescription}</p>
            {typeof requestId === "string" && requestId ? (
              <p className="mt-1 break-all text-xs text-fg-muted">
                {m.requestId}: <code>{requestId}</code>
              </p>
            ) : null}
          </Banner>
          <Button icon={ArrowRight} type="button" className="w-full" onClick={() => navigate(entryRoute(auth.hasPermission), { replace: true })}>
            {m.forbiddenBack}
          </Button>
        </CardContent>
      </Card>
    </AuthSurface>
  );
}
