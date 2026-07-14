import { useState } from "react";
import { useTranslation } from "react-i18next";

import { PROBLEM_CODES, toProblem } from "@/api/problems";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import { forgotPassword } from "./api";
import { AuthCard, AuthField, AuthFooter, AuthNotice } from "./AuthCard";

/** /forgot-password — the answer is deliberately uniform (anti-enumeration):
 * whatever the email, the screen says "if the account exists, a letter is out". */
export function ForgotPasswordPage() {
  const { t } = useTranslation();
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setSubmitting(true);
    setError(null);
    try {
      await forgotPassword(email);
      setSent(true);
    } catch (err) {
      const problem = await toProblem(err);
      setError(
        problem?.code === PROBLEM_CODES.RATE_LIMITED
          ? t("auth.forgot.errors.rateLimited", { seconds: problem.retry_after ?? 60 })
          : t("auth.forgot.errors.generic"),
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthCard title={t("auth.forgot.title")} subtitle={t("auth.forgot.subtitle")} error={error}>
      {sent ? (
        <AuthNotice>{t("auth.forgot.sent")}</AuthNotice>
      ) : (
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <AuthField htmlFor="forgot-email" label={t("auth.login.email")}>
            <Input
              id="forgot-email"
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
          <Button type="submit" size="lg" disabled={submitting} className="w-full">
            {t("auth.forgot.submit")}
          </Button>
        </form>
      )}

      <AuthFooter to="/login">{t("auth.forgot.backToLogin")}</AuthFooter>
    </AuthCard>
  );
}
