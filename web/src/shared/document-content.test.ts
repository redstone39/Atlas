import { afterEach, describe, expect, it, vi } from "vitest";

import {
  downloadDocumentContent,
  documentContentPath,
  safeDocumentFilename,
} from "./document-content";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("direct document content", () => {
  it("downloads the currently authorized original without creating a delivery", async () => {
    const click = vi.fn();
    const anchor = {
      click,
      href: "",
      download: "",
    } as unknown as HTMLAnchorElement;
    vi.spyOn(window.document, "createElement").mockReturnValue(anchor);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null)));

    await downloadDocumentContent("doc/a", "source.pdf");

    expect(documentContentPath("doc/a")).toBe(
      "/api/v1/library/documents/doc%2Fa/content",
    );
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/library/documents/doc%2Fa/content",
      { credentials: "include", method: "HEAD" },
    );
    expect(click).toHaveBeenCalledOnce();
    expect(anchor.href).toBe("/api/v1/library/documents/doc%2Fa/content");
    expect(anchor.download).toBe("source.pdf");
    expect(safeDocumentFilename('../../report"\r\n.pdf')).toBe("report_.pdf");
  });
});
