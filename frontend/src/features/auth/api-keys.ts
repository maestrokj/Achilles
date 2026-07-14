/** Mirrors backend/src/achilles/auth/routes/api_keys.py — the key contract is
 * shared by the admin's issuance screens and the user's own account card. */

export interface ApiKey {
  id: number;
  user_id: number;
  prefix: string;
  name: string | null;
  scope: { access: string; sources: number[] | null };
  expires_at: string | null;
  last_used_at: string | null;
  is_revoked: boolean;
  revoked_at: string | null;
  created_at: string;
}

export interface ApiKeyCreated extends ApiKey {
  key: string;
}

/** Backend contract: API_KEY_EXPIRY_CHOICES — 30 / 90 / 365 days or no expiry. */
export const API_KEY_EXPIRY_CHOICES = ["none", "30", "90", "365"] as const;
export type ApiKeyExpiry = (typeof API_KEY_EXPIRY_CHOICES)[number];

/** Backend contract: API_KEY_NAME_MAX_LEN — caps the optional label input. */
export const API_KEY_NAME_MAX_LEN = 80;

/** The request body fragment both issuance dialogs send. */
export function expiryPayload(expiry: ApiKeyExpiry): { expires_in_days?: number } {
  return expiry === "none" ? {} : { expires_in_days: Number(expiry) };
}
