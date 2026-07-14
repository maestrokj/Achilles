import { useTranslation } from "react-i18next";

/** A lightweight strength hint for the password fields (setup / invite / reset).
 * The real gate is the backend (zxcvbn, 422); this only nudges the user before
 * they submit. Score 0–4 from length and character variety. */

const STRONG_LENGTH = 12;
const FAIR_LENGTH = 8;
const SEGMENTS = 4;

function scorePassword(password: string): number {
  if (password.length === 0) return 0;
  let variety = 0;
  if (/[a-z]/.test(password)) variety += 1;
  if (/[A-Z]/.test(password)) variety += 1;
  if (/\d/.test(password)) variety += 1;
  if (/[^A-Za-z0-9]/.test(password)) variety += 1;

  if (password.length < FAIR_LENGTH) return 1;
  if (password.length >= STRONG_LENGTH && variety >= 3) return 4;
  if (password.length >= STRONG_LENGTH || variety >= 3) return 3;
  return 2;
}

// Indexed by score (slot 0 = empty, never rendered). `bar` fills the active
// segments, `text` tints the label; the two stay aligned in one table.
const LEVELS = [
  { bar: "bg-muted", text: "" },
  { bar: "bg-destructive", text: "text-destructive" },
  { bar: "bg-warning", text: "text-warning" },
  { bar: "bg-warning", text: "text-warning" },
  { bar: "bg-success", text: "text-success" },
] as const;

export function PasswordStrengthMeter({ password }: { password: string }) {
  const { t } = useTranslation();
  const score = scorePassword(password);
  if (password.length === 0) return null;

  // Static keys indexed by score — a computed key would trip i18next's strict types.
  const label = [
    "",
    t("auth.password.strength.weak"),
    t("auth.password.strength.fair"),
    t("auth.password.strength.good"),
    t("auth.password.strength.strong"),
  ][score];

  return (
    <div className="flex flex-col gap-1.5" aria-live="polite">
      <div className="flex gap-1">
        {Array.from({ length: SEGMENTS }, (_, i) => (
          <span
            key={i}
            className={`h-1 flex-1 rounded-full ${i < score ? LEVELS[score].bar : LEVELS[0].bar}`}
          />
        ))}
      </div>
      <span className={`text-xs ${LEVELS[score].text}`}>
        {t("auth.password.strength.label")}: {label}
      </span>
    </div>
  );
}
