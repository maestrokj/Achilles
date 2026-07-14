import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router-dom";

import { PROBLEM_CODES, toProblem } from "@/api/problems";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { homePath } from "@/features/app-shell/home";

import { acceptInvite } from "./api";
import { AuthCard, AuthField, AuthFooter, AuthMessage, PasswordField } from "./AuthCard";

/** /invite/:token — registration by the one-time letter link: name + password.
 * Wireframe: auth-security/_wireframes/invite-accept.html; a dead link gets
 * a plain expired state instead of the form. */
export function InviteAcceptPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { token = "" } = useParams();

  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [dead, setDead] = useState<"expired" | "used" | "taken" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (password !== confirm) {
      setError(t("auth.invite.errors.mismatch"));
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const data = await acceptInvite(token, fullName, password);
      void navigate(homePath(data.user.role), { replace: true });
    } catch (err) {
      const problem = await toProblem(err);
      if (problem?.code === PROBLEM_CODES.INVITE_EXPIRED) setDead("expired");
      else if (problem?.code === PROBLEM_CODES.INVITE_USED) setDead("used");
      else if (problem?.code === PROBLEM_CODES.EMAIL_TAKEN) setDead("taken");
      else if (problem?.errors?.length)
        setError(
          problem.errors[0].field === "password"
            ? t("errors.validation.weakPassword")
            : t("errors.status.validation"),
        );
      else setError(t("auth.invite.errors.generic"));
    } finally {
      setSubmitting(false);
    }
  }

  if (dead) {
    return (
      <AuthCard title={t(`auth.invite.${dead}Title`)}>
        <AuthMessage
          body={t(dead === "taken" ? "auth.invite.takenBody" : "auth.invite.deadBody")}
          to="/login"
          linkLabel={t("auth.invite.toLogin")}
        />
      </AuthCard>
    );
  }

  return (
    <AuthCard title={t("auth.invite.title")} subtitle={t("auth.invite.subtitle")} error={error}>
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <AuthField htmlFor="invite-name" label={t("auth.invite.fullName")}>
          <Input
            id="invite-name"
            autoComplete="name"
            required
            value={fullName}
            onChange={(event) => {
              setFullName(event.target.value);
            }}
          />
        </AuthField>
        <PasswordField
          id="invite-password"
          label={t("auth.invite.password")}
          autoComplete="new-password"
          showStrength
          value={password}
          onChange={setPassword}
        />
        <PasswordField
          id="invite-confirm"
          label={t("auth.invite.confirmPassword")}
          autoComplete="new-password"
          value={confirm}
          onChange={setConfirm}
        />
        <Button type="submit" size="lg" disabled={submitting} className="w-full">
          {t("auth.invite.submit")}
        </Button>
      </form>

      <AuthFooter to="/login">{t("auth.invite.haveAccount")}</AuthFooter>
    </AuthCard>
  );
}
