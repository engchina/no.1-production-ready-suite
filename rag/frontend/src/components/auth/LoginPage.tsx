"use client";

import { Eye, EyeOff, LockKeyhole, LogIn } from "lucide-react";
import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { t } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";

const REMEMBER_ME_STORAGE_KEY = "production-ready-rag.rememberMe";
const REMEMBERED_USERNAME_STORAGE_KEY = "production-ready-rag.rememberedUsername";

/** production mode のログイン画面。 */
export function LoginPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [rememberMe, setRememberMe] = useState(true);
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    try {
      const persistedRememberMe = window.localStorage.getItem(REMEMBER_ME_STORAGE_KEY);
      const shouldRemember = persistedRememberMe !== "false";
      setRememberMe(shouldRemember);
      if (shouldRemember) {
        setUsername(window.localStorage.getItem(REMEMBERED_USERNAME_STORAGE_KEY) ?? "");
      }
    } catch {
      // localStorage が無効でもログイン操作は継続できる。
    }
  }, []);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    try {
      await auth.login({ username, password, remember_me: rememberMe });
      persistRememberedUser(username, rememberMe);
      const redirectTarget = (location.state as { from?: string } | null)?.from;
      navigate(
        redirectTarget && redirectTarget !== APP_ROUTES.login
          ? redirectTarget
          : APP_ROUTES.dashboard,
        { replace: true }
      );
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : t("auth.login.error.failed")
      );
    }
  }

  function handleRememberMeChange(nextChecked: boolean) {
    setRememberMe(nextChecked);
    if (!nextChecked) {
      persistRememberedUser("", false);
    }
  }

  return (
    <main className="grid min-h-dvh place-items-center bg-background px-4 py-8">
      <section
        className="w-full max-w-[420px] rounded-lg border border-border bg-card p-6 shadow-sm"
        aria-labelledby="login-title"
      >
        <div className="mb-6 flex items-start gap-3">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-md bg-info-bg text-info">
            <LockKeyhole size={22} aria-hidden />
          </div>
          <div>
            <h1 id="login-title" className="text-xl font-bold leading-7 text-foreground">
              {t("auth.login.title")}
            </h1>
            <p className="mt-1 text-sm leading-6 text-muted">{t("auth.login.subtitle")}</p>
          </div>
        </div>

        <form className="space-y-4" onSubmit={handleSubmit}>
          {error ? (
            <div
              className="rounded-md border border-danger/30 bg-danger-bg px-3 py-2 text-sm text-danger"
              role="alert"
            >
              {error}
            </div>
          ) : null}

          <div className="space-y-2">
            <label htmlFor="login-username" className="text-sm font-medium text-foreground">
              {t("auth.login.username")}
              <span className="ml-1 text-danger" aria-hidden>
                *
              </span>
            </label>
            <input
              id="login-username"
              type="text"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              className="h-11 w-full rounded-md border border-border bg-background px-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted focus:border-ring"
              placeholder={t("auth.login.usernamePlaceholder")}
              autoComplete="username"
              required
            />
          </div>

          <div className="space-y-2">
            <label htmlFor="login-password" className="text-sm font-medium text-foreground">
              {t("auth.login.password")}
              <span className="ml-1 text-danger" aria-hidden>
                *
              </span>
            </label>
            <div className="relative">
              <input
                id="login-password"
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="h-11 w-full rounded-md border border-border bg-background px-3 pr-12 text-sm text-foreground outline-none transition-colors placeholder:text-muted focus:border-ring"
                placeholder={t("auth.login.passwordPlaceholder")}
                autoComplete="current-password"
                required
              />
              <button
                type="button"
                className="absolute right-0 top-0 inline-flex h-11 w-11 cursor-pointer items-center justify-center rounded-md text-muted transition-colors hover:text-foreground"
                aria-label={
                  showPassword ? t("auth.login.hidePassword") : t("auth.login.showPassword")
                }
                onClick={() => setShowPassword((current) => !current)}
              >
                {showPassword ? <EyeOff size={18} aria-hidden /> : <Eye size={18} aria-hidden />}
              </button>
            </div>
          </div>

          <label className="flex cursor-pointer items-center gap-3 text-sm text-foreground">
            <input
              type="checkbox"
              checked={rememberMe}
              onChange={(event) => handleRememberMeChange(event.target.checked)}
              className="h-4 w-4 cursor-pointer accent-[var(--primary)]"
            />
            <span>{t("auth.login.rememberMe")}</span>
          </label>

          <Button type="submit" className="h-11 w-full" loading={auth.isLoggingIn}>
            <LogIn size={16} aria-hidden />
            {auth.isLoggingIn ? t("auth.login.signingIn") : t("auth.login.signIn")}
          </Button>
        </form>
      </section>
    </main>
  );
}

function persistRememberedUser(username: string, rememberMe: boolean) {
  try {
    if (rememberMe) {
      window.localStorage.setItem(REMEMBER_ME_STORAGE_KEY, "true");
      window.localStorage.setItem(REMEMBERED_USERNAME_STORAGE_KEY, username);
      return;
    }
    window.localStorage.setItem(REMEMBER_ME_STORAGE_KEY, "false");
    window.localStorage.removeItem(REMEMBERED_USERNAME_STORAGE_KEY);
  } catch {
    // 保存できなくても認証フロー自体には影響させない。
  }
}
