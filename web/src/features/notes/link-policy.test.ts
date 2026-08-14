import { describe, expect, it } from "vitest";

import { isAllowedNoteLink } from "./link-policy";

describe("Notes link policy", () => {
  it("allows only supported string URLs and fails closed for malformed attributes", () => {
    expect(isAllowedNoteLink(" https://example.com ")).toBe(true);
    expect(isAllowedNoteLink("mailto:notes@example.com")).toBe(true);
    expect(isAllowedNoteLink("javascript:alert(1)")).toBe(false);
    expect(isAllowedNoteLink({ href: "https://example.com" })).toBe(false);
    expect(isAllowedNoteLink(42)).toBe(false);
    expect(isAllowedNoteLink(null)).toBe(false);
  });
});
