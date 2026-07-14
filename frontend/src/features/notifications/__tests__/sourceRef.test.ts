import { describe, expect, it } from "vitest";

import { sourceRefPath } from "../sourceRef";

describe("sourceRefPath", () => {
  it("routes admin-surface refs to their Admin Panel screens", () => {
    expect(sourceRefPath("source/42", "admin")).toBe("/admin/harvester/sources/42");
    expect(sourceRefPath("user/9", "admin")).toBe("/admin/users/9");
    expect(sourceRefPath("agent/7", "admin")).toBe("/admin/agents/7");
    // Id-less refs still resolve to their fixed screen.
    expect(sourceRefPath("ai-usage", "admin")).toBe("/admin/ai-usage");
    expect(sourceRefPath("api-key/3", "admin")).toBe("/admin/api-keys");
  });

  it("routes app-surface refs to the employee's own screens", () => {
    expect(sourceRefPath("agent/7", "app")).toBe("/agents/7");
    expect(sourceRefPath("api-key/3", "app")).toBe("/account");
  });

  it("returns null for refs the surface does not carry", () => {
    // `source` is an admin-only target — the personal surface has no place for it.
    expect(sourceRefPath("source/42", "app")).toBeNull();
    expect(sourceRefPath("user/9", "app")).toBeNull();
  });

  it("returns null for a missing or unknown ref", () => {
    expect(sourceRefPath(null, "admin")).toBeNull();
    expect(sourceRefPath("mystery/1", "admin")).toBeNull();
  });
});
