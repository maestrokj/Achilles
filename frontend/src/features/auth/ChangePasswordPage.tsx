import { useState } from "react";
import { useTranslation } from "react-i18next";

import { apiErrorReason } from "@/api/errors";
import { PROBLEM_CODES, toProblem } from "@/api/problems";
import { Button } from "@/components/ui/button";

import { changePassword, logout } from "./api";
import { AuthCard, AuthFooter, PasswordField } from "./AuthCard";

/** Forced stop while must_change_password is set: the backend answers only the
 * change/logout routes, so nothing else renders until the password is replaced. */
export function ChangePasswordPage() {
  const { t } = useTranslation();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (newPassword !== confirmPassword) {
      setError(t("auth.changePassword.errors.mismatch"));
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await changePassword(currentPassword, newPassword);
    } catch (err) {
      const problem = await toProblem(err);
      if (problem?.code === PROBLEM_CODES.INVALID_CREDENTIALS) {
        setError(t("auth.changePassword.errors.wrongCurrent"));
      } else if (problem?.errors?.length) {
        setError(
          problem.errors[0].field === "password"
            ? t("errors.validation.weakPassword")
            : t("errors.status.validation"),
        );
      } else {
        setError(await apiErrorReason(err));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthCard
      title={t("auth.changePassword.title")}
      subtitle={t("auth.changePassword.subtitle")}
      error={error}
    >
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <PasswordField
          id="current-password"
          label={t("auth.changePassword.current")}
          autoComplete="current-password"
          value={currentPassword}
          onChange={setCurrentPassword}
        />

        <PasswordField
          id="new-password"
          label={t("auth.changePassword.new")}
          autoComplete="new-password"
          showStrength
          value={newPassword}
          onChange={setNewPassword}
        />

        <PasswordField
          id="confirm-password"
          label={t("auth.changePassword.confirm")}
          autoComplete="new-password"
          value={confirmPassword}
          onChange={setConfirmPassword}
        />

        <Button type="submit" size="lg" disabled={submitting} className="w-full">
          {t("auth.changePassword.submit")}
        </Button>
      </form>

      <AuthFooter
        onClick={() => {
          void logout();
        }}
      >
        {t("auth.changePassword.signOut")}
      </AuthFooter>
    </AuthCard>
  );
}
