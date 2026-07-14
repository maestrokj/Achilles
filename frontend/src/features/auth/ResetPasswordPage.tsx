import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router-dom";

import { PROBLEM_CODES, toProblem } from "@/api/problems";
import { Button } from "@/components/ui/button";

import { resetPassword } from "./api";
import { AuthCard, AuthMessage, PasswordField } from "./AuthCard";

/** /reset-password/:token — the letter's landing: two password fields.
 * A used and an expired link are indistinguishable on purpose (410). */
export function ResetPasswordPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { token = "" } = useParams();

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [expired, setExpired] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (password !== confirm) {
      setError(t("auth.reset.errors.mismatch"));
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await resetPassword(token, password);
      void navigate("/login", { replace: true });
    } catch (err) {
      const problem = await toProblem(err);
      if (problem?.code === PROBLEM_CODES.RESET_EXPIRED) setExpired(true);
      else if (problem?.errors?.length)
        setError(
          problem.errors[0].field === "password"
            ? t("errors.validation.weakPassword")
            : t("errors.status.validation"),
        );
      else setError(t("auth.reset.errors.generic"));
    } finally {
      setSubmitting(false);
    }
  }

  if (expired) {
    return (
      <AuthCard title={t("auth.reset.expiredTitle")}>
        <AuthMessage
          body={t("auth.reset.expiredBody")}
          to="/forgot-password"
          linkLabel={t("auth.reset.requestAgain")}
        />
      </AuthCard>
    );
  }

  return (
    <AuthCard title={t("auth.reset.title")} error={error}>
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <PasswordField
          id="reset-password"
          label={t("auth.reset.newPassword")}
          autoComplete="new-password"
          showStrength
          value={password}
          onChange={setPassword}
        />
        <PasswordField
          id="reset-confirm"
          label={t("auth.reset.confirmPassword")}
          autoComplete="new-password"
          value={confirm}
          onChange={setConfirm}
        />
        <Button type="submit" size="lg" disabled={submitting} className="w-full">
          {t("auth.reset.submit")}
        </Button>
      </form>
    </AuthCard>
  );
}
