import { useState, type FormEvent, type ReactNode } from "react";
import { KeyRound, LogIn, ShieldCheck } from "lucide-react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { Banner, Button, Card, CardContent, CardHeader, CardTitle, FormStatus } from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";
import { securityApi } from "./api";
import { firstAllowedRoute } from "./route-permissions";
import { useAuth } from "./AuthProvider";

const INPUT_CLASS =
  "h-11 w-full rounded-md border border-border bg-card px-3 text-sm text-foreground outline-none transition focus:border-primary focus:ring-2 focus:ring-ring/30 disabled:opacity-60";

function AuthSurface({ children }: { children: ReactNode }) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4 py-10">
      <div className="w-full max-w-md space-y-5">
        <div className="flex items-center justify-center gap-3 text-center">
          <span className="inline-flex h-11 w-11 items-center justify-center rounded-xl bg-primary text-primary-foreground">
            <ShieldCheck size={22} aria-hidden />
          </span>
          <div className="text-left">
            <p className="text-sm font-semibold text-foreground">{t("app.sidebarTitle.line1")}</p>
            <p className="text-xs text-muted">{t("app.sidebarTitle.line2")}</p>
          </div>
        </div>
        {children}
      </div>
    </main>
  );
}

export function LoginPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [loginName, setLoginName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  if (auth.status === "authenticated") {
    return <Navigate to={auth.user?.force_password_change ? APP_ROUTES.passwordChange : firstAllowedRoute(auth.hasPermission)} replace />;
  }

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!loginName.trim() || !password) {
      setError(t("auth.login.required"));
      return;
    }
    setBusy(true);
    setError("");
    try {
      const current = await auth.login(loginName, password);
      const requested = (location.state as { from?: string } | null)?.from;
      const canAccess = (permission: string) =>
        current.role_codes.includes("SYSTEM_ADMIN") || current.permissions.includes(permission);
      navigate(
        current.force_password_change
          ? APP_ROUTES.passwordChange
          : requested || firstAllowedRoute(canAccess),
        { replace: true }
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("auth.login.error"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthSurface>
      <Card>
        <CardHeader>
          <CardTitle>{t("auth.login.title")}</CardTitle>
          <p className="text-sm leading-6 text-muted">{t("auth.login.subtitle")}</p>
        </CardHeader>
        <CardContent>
          <form className="space-y-4" onSubmit={handleSubmit} noValidate>
            {error ? <Banner severity="danger">{error}</Banner> : null}
            <label className="block space-y-1.5 text-sm font-medium">
              <span>{t("auth.login.name")}</span>
              <input
                autoComplete="username"
                autoFocus
                className={INPUT_CLASS}
                value={loginName}
                onChange={(event) => setLoginName(event.target.value)}
              />
            </label>
            <label className="block space-y-1.5 text-sm font-medium">
              <span>{t("auth.login.password")}</span>
              <input
                type="password"
                autoComplete="current-password"
                className={INPUT_CLASS}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </label>
            {/* 認証の主導線はモバイルでも 44px のタッチ領域を確保する。 */}
            <Button className="h-11 w-full" loading={busy} type="submit">
              <LogIn size={16} aria-hidden />
              {t("auth.login.submit")}
            </Button>
          </form>
        </CardContent>
      </Card>
    </AuthSurface>
  );
}

export function PasswordChangePage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  if (auth.status === "unauthenticated") return <Navigate to={APP_ROUTES.login} replace />;
  if (auth.user?.debug_mode) {
    return <Navigate to={firstAllowedRoute(auth.hasPermission)} replace />;
  }

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (newPassword !== confirmation) {
      setError(t("auth.password.mismatch"));
      return;
    }
    setBusy(true);
    setError("");
    try {
      await securityApi.changePassword(currentPassword, newPassword);
      setSuccess(t("auth.password.changed"));
      window.setTimeout(() => {
        void auth.refresh().finally(() => navigate(APP_ROUTES.login, { replace: true }));
      }, 900);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("security.common.saveError"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthSurface>
      <Card>
        <CardHeader>
          <CardTitle>{t("auth.password.title")}</CardTitle>
          <p className="text-sm leading-6 text-muted">{t("auth.password.subtitle")}</p>
        </CardHeader>
        <CardContent>
          <form className="space-y-4" onSubmit={handleSubmit}>
            {error ? <Banner severity="danger">{error}</Banner> : null}
            <Banner severity="info">{t("auth.password.rule")}</Banner>
            {[
              [t("auth.password.current"), currentPassword, setCurrentPassword, "current-password"],
              [t("auth.password.new"), newPassword, setNewPassword, "new-password"],
              [t("auth.password.confirm"), confirmation, setConfirmation, "new-password"],
            ].map(([label, value, setter, autoComplete]) => (
              <label key={String(label)} className="block space-y-1.5 text-sm font-medium">
                <span>{String(label)}</span>
                <input
                  required
                  type="password"
                  className={INPUT_CLASS}
                  autoComplete={String(autoComplete)}
                  value={String(value)}
                  onChange={(event) => (setter as (value: string) => void)(event.target.value)}
                />
              </label>
            ))}
            <div className="space-y-2 border-t border-border pt-4">
              <Button className="h-11 w-full" loading={busy} type="submit">
                <KeyRound size={16} aria-hidden />
                {t("auth.password.submit")}
              </Button>
              <FormStatus tone="success" message={success} />
            </div>
          </form>
        </CardContent>
      </Card>
    </AuthSurface>
  );
}

export function ForbiddenPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  return (
    <AuthSurface>
      <Card>
        <CardHeader>
          <CardTitle>{t("auth.forbidden.title")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          <Banner severity="warning">{t("auth.forbidden.description")}</Banner>
          <Button className="w-full" onClick={() => navigate(firstAllowedRoute(auth.hasPermission), { replace: true })}>
            {t("auth.forbidden.back")}
          </Button>
        </CardContent>
      </Card>
    </AuthSurface>
  );
}
