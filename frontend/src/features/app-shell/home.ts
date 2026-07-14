import { type Role, isMember } from "@/features/auth/roles";

/** Landing surface per role: members live in the chat, staff in the admin panel. */
export function homePath(role: Role): string {
  return isMember(role) ? "/chat" : "/admin";
}
