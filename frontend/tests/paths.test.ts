/** Story 6.1: links follow the base path that is passed in. */
import { describe, expect, it } from "vitest";
import { assistantPaths, joinPath } from "@/lib/assistant/paths";

describe("assistantPaths", () => {
  it("builds every page link from the base path", () => {
    expect(assistantPaths("/assistant")).toEqual({
      assistant: "/assistant",
      settings: "/assistant/settings",
      requests: "/assistant/requests",
      organisation: "/assistant/organisation",
    });
  });

  it("follows another path, with or without slashes", () => {
    expect(assistantPaths("/flow/desks/ea/").settings).toBe("/flow/desks/ea/settings");
    expect(joinPath("flow/desk", "/requests/")).toBe("/flow/desk/requests");
    expect(joinPath("/", "")).toBe("/");
  });
});
