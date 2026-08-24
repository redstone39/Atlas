import { beforeEach, describe, expect, it, vi } from "vitest";

import { requestJson } from "./api-client";

describe("requestJson", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })));
  });

  it("keeps mutation headers while defaulting string bodies to JSON", async () => {
    await requestJson("/api/v1/notes", {
      method: "POST",
      headers: {
        "Idempotency-Key": "post-request-key",
        "If-Match": "7",
      },
      body: "{}",
    });

    const [, request] = vi.mocked(fetch).mock.calls[0];
    const headers = new Headers(request?.headers);
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.get("Idempotency-Key")).toBe("post-request-key");
    expect(headers.get("If-Match")).toBe("7");
    expect(request).toEqual(expect.objectContaining({ credentials: "include" }));
  });

  it("preserves an explicit content type", async () => {
    await requestJson("/api/v1/custom", {
      method: "POST",
      headers: { "Content-Type": "application/merge-patch+json" },
      body: "{}",
    });

    const [, request] = vi.mocked(fetch).mock.calls[0];
    expect(new Headers(request?.headers).get("Content-Type"))
      .toBe("application/merge-patch+json");
  });

  it("leaves multipart content type unset so fetch can add its boundary", async () => {
    const body = new FormData();
    body.set("file", new Blob(["content"]), "note.txt");

    await requestJson("/api/v1/upload", {
      method: "POST",
      headers: { "Idempotency-Key": "upload-key" },
      body,
    });

    const [, request] = vi.mocked(fetch).mock.calls[0];
    const headers = new Headers(request?.headers);
    expect(headers.has("Content-Type")).toBe(false);
    expect(headers.get("Idempotency-Key")).toBe("upload-key");
  });
});
