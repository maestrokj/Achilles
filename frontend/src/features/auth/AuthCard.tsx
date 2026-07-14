import { EyeIcon, EyeOffIcon } from "lucide-react";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { BrandMark } from "@/components/BrandMark";
import { PasswordStrengthMeter } from "@/components/PasswordStrengthMeter";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/** The auth-screen kit: one card shell plus the small pieces every screen
 * reuses — a field, a password input with a reveal toggle, a neutral notice,
 * a footer link, and the terminal (expired / done) message. Keeping them here
 * is what makes the six auth screens read as one family. */

/** Centered card shell: warm halo, brand + heading, error slot, body. */
export function AuthCard({
  title,
  subtitle,
  error,
  children,
}: {
  title: string;
  subtitle?: string;
  error?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="bg-background text-foreground relative flex min-h-screen items-center justify-center overflow-hidden px-4 py-10">
      {/* Warm halo — a single soft glow behind the card; pure atmosphere. */}
      <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 flex justify-center">
        <div className="bg-primary/[0.07] size-[38rem] -translate-y-1/3 rounded-full blur-3xl" />
      </div>

      <div className="motion-safe:animate-in motion-safe:fade-in motion-safe:slide-in-from-bottom-3 relative w-full max-w-[25rem] motion-safe:duration-500">
        <div className="border-border/70 bg-card flex flex-col gap-6 rounded-3xl border p-8 shadow-xl shadow-black/[0.04]">
          <div className="flex flex-col items-center gap-4 text-center">
            <BrandMark className="text-base" />
            <div className="flex flex-col gap-1.5">
              <h1 className="font-serif text-[1.6rem] leading-tight font-medium tracking-tight">
                {title}
              </h1>
              {subtitle && <p className="text-muted-foreground text-sm text-balance">{subtitle}</p>}
            </div>
          </div>

          {error && (
            <p className="border-destructive/25 bg-destructive/10 text-destructive rounded-xl border px-3.5 py-2.5 text-sm">
              {error}
            </p>
          )}

          {children}
        </div>
      </div>
    </div>
  );
}

/** A labelled form row — the shared field rhythm across every screen. */
export function AuthField({
  htmlFor,
  label,
  children,
}: {
  htmlFor: string;
  label: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
    </div>
  );
}

/** Password input with a built-in show/hide toggle and an optional strength
 * meter — used wherever a password is typed, so the affordance never drifts. */
export function PasswordField({
  id,
  label,
  value,
  onChange,
  autoComplete = "current-password",
  showStrength = false,
}: {
  id: string;
  label: ReactNode;
  value: string;
  onChange: (value: string) => void;
  autoComplete?: string;
  showStrength?: boolean;
}) {
  const { t } = useTranslation();
  const [reveal, setReveal] = useState(false);

  return (
    <AuthField htmlFor={id} label={label}>
      <div className="relative">
        <Input
          id={id}
          type={reveal ? "text" : "password"}
          autoComplete={autoComplete}
          required
          className="pr-9"
          value={value}
          onChange={(event) => {
            onChange(event.target.value);
          }}
        />
        <button
          type="button"
          className="text-muted-foreground hover:text-foreground absolute inset-y-0 right-0 flex w-9 items-center justify-center transition-colors"
          aria-label={reveal ? t("auth.password.hide") : t("auth.password.show")}
          onClick={() => {
            setReveal((value) => !value);
          }}
        >
          {reveal ? <EyeOffIcon className="size-4" /> : <EyeIcon className="size-4" />}
        </button>
      </div>
      {showStrength && <PasswordStrengthMeter password={value} />}
    </AuthField>
  );
}

/** A calm neutral panel — expired-session hints, "letter is out" confirmations. */
export function AuthNotice({ children }: { children: ReactNode }) {
  return (
    <p className="border-border bg-muted text-muted-foreground rounded-xl border px-3.5 py-2.5 text-sm">
      {children}
    </p>
  );
}

const footerLinkClass =
  "text-muted-foreground hover:text-foreground underline underline-offset-4 transition-colors";

/** The shared footer action — centered, muted, underlined. Navigates via `to`,
 * or acts via `onClick` (e.g. sign out) with the same look. */
export function AuthFooter({
  to,
  onClick,
  children,
}: {
  to?: string;
  onClick?: () => void;
  children: ReactNode;
}) {
  return (
    <p className="text-center text-sm">
      {to ? (
        <Link to={to} className={footerLinkClass}>
          {children}
        </Link>
      ) : (
        <button type="button" onClick={onClick} className={`${footerLinkClass} cursor-pointer`}>
          {children}
        </button>
      )}
    </p>
  );
}

/** Terminal state (expired link, already set up, …): a centered line + a way out. */
export function AuthMessage({
  body,
  to,
  linkLabel,
}: {
  body: ReactNode;
  to: string;
  linkLabel: ReactNode;
}) {
  return (
    <>
      <p className="text-muted-foreground text-center text-sm text-balance">{body}</p>
      <AuthFooter to={to}>{linkLabel}</AuthFooter>
    </>
  );
}
