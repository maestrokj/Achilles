import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";

import { PROBLEM_CODES, toProblem } from "@/api/problems";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { homePath } from "@/features/app-shell/home";

import { login } from "./api";
import { AuthCard, AuthField, AuthFooter, AuthNotice, PasswordField } from "./AuthCard";
import { useSession } from "./session-context";
import { SESSION_EXPIRED_REASON } from "./session-store";

type LoginError = "invalidCredentials" | "rateLimited" | "generic";

/** null → fall back to the role home once the role is known. */
function safeReturnTo(raw: string | null): string | null {
  return raw && raw.startsWith("/") && !raw.startsWith("//") ? raw : null;
}

export function LoginPage() {
  const { t } = useTranslation();
  const session = useSession();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const returnTo = safeReturnTo(searchParams.get("returnTo"));
  const sessionExpired = searchParams.get("reason") === SESSION_EXPIRED_REASON;

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [rememberMe, setRememberMe] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<LoginError | null>(null);
  const [retryLeft, setRetryLeft] = useState(0);

  useEffect(() => {
    if (retryLeft <= 0) return;
    const timer = setInterval(() => {
      setRetryLeft((left) => Math.max(0, left - 1));
    }, 1000);
    return () => {
      clearInterval(timer);
    };
  }, [retryLeft]);

  if (session.status === "authenticated") {
    return <Navigate to={returnTo ?? homePath(session.user.role)} replace />;
  }

  async function submit() {
    setSubmitting(true);
    setError(null);
    try {
      const data = await login({ email, password, rememberMe });
      void navigate(returnTo ?? homePath(data.user.role), { replace: true });
    } catch (err) {
      const problem = await toProblem(err);
      if (problem?.code === PROBLEM_CODES.INVALID_CREDENTIALS) {
        setError("invalidCredentials");
      } else if (problem?.code === PROBLEM_CODES.RATE_LIMITED) {
        setError("rateLimited");
        setRetryLeft(problem.retry_after ?? 60);
      } else {
        setError("generic");
      }
    } finally {
      setSubmitting(false);
    }
  }

  const rateLimited = error === "rateLimited" && retryLeft > 0;

  return (
    <AuthCard
      title={t("auth.login.title")}
      error={
        error &&
        (error === "rateLimited"
          ? t("auth.login.errors.rateLimited", { seconds: retryLeft })
          : t(`auth.login.errors.${error}`))
      }
    >
      {sessionExpired && !error && <AuthNotice>{t("auth.login.sessionExpired")}</AuthNotice>}

      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <AuthField htmlFor="login-email" label={t("auth.login.email")}>
          <Input
            id="login-email"
            type="email"
            autoComplete="email"
            required
            placeholder={t("auth.login.emailPlaceholder")}
            value={email}
            onChange={(event) => {
              setEmail(event.target.value);
            }}
          />
        </AuthField>

        <PasswordField
          id="login-password"
          label={t("auth.login.password")}
          value={password}
          onChange={setPassword}
        />

        <div className="flex items-center gap-2">
          <Checkbox
            id="login-remember"
            checked={rememberMe}
            onCheckedChange={(checked) => {
              setRememberMe(checked);
            }}
          />
          <Label htmlFor="login-remember" className="text-muted-foreground font-normal">
            {t("auth.login.rememberMe")}
          </Label>
        </div>

        <Button type="submit" size="lg" disabled={submitting || rateLimited} className="w-full">
          {t("auth.login.submit")}
        </Button>

        <AuthFooter to="/forgot-password">{t("auth.login.forgotPassword")}</AuthFooter>
      </form>

      <div className="flex items-center gap-3">
        <span className="bg-border h-px flex-1" />
        <span className="text-muted-foreground text-xs">{t("auth.login.or")}</span>
        <span className="bg-border h-px flex-1" />
      </div>

      <Button variant="outline" disabled className="w-full">
        {t("auth.login.sso")}
      </Button>
    </AuthCard>
  );
}
