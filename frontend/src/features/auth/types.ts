/** Mirrors backend/src/achilles/auth/schemas.py (UserOut / SessionResponse). */

import type { Role } from "@/features/auth/roles";

export interface SessionUser {
  id: number;
  email: string;
  full_name: string;
  role: Role;
  status: string;
  must_change_password: boolean;
  timezone: string | null;
  locale: string | null;
  date_format: string | null;
  last_login_at: string | null;
  created_at: string;
}

export interface SessionResponse {
  access_token: string;
  token_type: string;
  must_change_password: boolean;
  user: SessionUser;
}
