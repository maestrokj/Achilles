import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useNavigate } from "react-router-dom";

import { PROBLEM_CODES, toProblem } from "@/api/problems";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { homePath } from "@/features/app-shell/home";

import { setup } from "./api";
import { AuthCard, AuthField, AuthMessage, PasswordField } from "./AuthCard";
import { useSession } from "./session-context";

/** /setup — first-run: create the Owner on an empty database.
 * Wireframe: auth-security/_wireframes/setup-wizard.html. Once any account
 * exists the backend answers 404 SETUP_UNAVAILABLE and we show the done state. */
export function SetupWizardPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const session = useSession();

  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (session.status === "authenticated") {
    return <Navigate to={homePath(session.user.role)} replace />;
  }

  async function submit() {
    if (password !== confirm) {
      setError(t("auth.setup.errors.mismatch"));
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const data = await setup({ email, fullName, password });
      void navigate(homePath(data.user.role), { replace: true });
    } catch (err) {
      const problem = await toProblem(err);
      if (problem?.code === PROBLEM_CODES.SETUP_UNAVAILABLE) setDone(true);
      else if (problem?.errors?.length)
        setError(
          problem.errors[0].field === "password"
            ? t("errors.validation.weakPassword")
            : t("errors.status.validation"),
        );
      else setError(t("auth.setup.errors.generic"));
    } finally {
      setSubmitting(false);
    }
  }

  if (done) {
    return (
      <AuthCard title={t("auth.setup.doneTitle")}>
        <AuthMessage
          body={t("auth.setup.doneBody")}
          to="/login"
          linkLabel={t("auth.setup.toLogin")}
        />
      </AuthCard>
    );
  }

  return (
    <AuthCard title={t("auth.setup.title")} subtitle={t("auth.setup.subtitle")} error={error}>
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <span className="bg-secondary text-secondary-foreground self-start rounded-full px-2.5 py-1 text-xs font-medium">
          {t("auth.setup.ownerBadge")}
        </span>
        <AuthField htmlFor="setup-name" label={t("auth.setup.fullName")}>
          <Input
            id="setup-name"
            autoComplete="name"
            required
            value={fullName}
            onChange={(event) => {
              setFullName(event.target.value);
            }}
          />
        </AuthField>
        <AuthField htmlFor="setup-email" label={t("auth.setup.email")}>
          <Input
            id="setup-email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => {
              setEmail(event.target.value);
            }}
          />
        </AuthField>
        <PasswordField
          id="setup-password"
          label={t("auth.setup.password")}
          autoComplete="new-password"
          showStrength
          value={password}
          onChange={setPassword}
        />
        <PasswordField
          id="setup-confirm"
          label={t("auth.setup.confirmPassword")}
          autoComplete="new-password"
          value={confirm}
          onChange={setConfirm}
        />
        <Button type="submit" size="lg" disabled={submitting} className="w-full">
          {t("auth.setup.submit")}
        </Button>
      </form>
    </AuthCard>
  );
}
