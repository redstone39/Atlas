import { describe, expect, it, vi } from "vitest";

import { SessionQueryClient } from "./session-query-client";
import { ApiError } from "./user-messages";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve;
    reject = nextReject;
  });
  return { promise, reject, resolve };
}

describe("SessionQueryClient", () => {
  it("deduplicates simultaneous consumers without retaining completed data", async () => {
    const client = new SessionQueryClient();
    const first = deferred<string>();
    const queryFn = vi.fn(() => first.promise);

    const one = client.query({ key: ["teams"], queryFn });
    const two = client.query({ key: ["teams"], queryFn });
    first.resolve("ready");

    await expect(Promise.all([one, two])).resolves.toEqual(["ready", "ready"]);
    expect(queryFn).toHaveBeenCalledTimes(1);

    await client.query({ key: ["teams"], queryFn: async () => "fresh" });
    expect(queryFn).toHaveBeenCalledTimes(1);
  });

  it("keeps shared work alive until the last consumer leaves", async () => {
    const client = new SessionQueryClient();
    const work = deferred<string>();
    let underlyingSignal: AbortSignal | undefined;
    const firstConsumer = new AbortController();
    const secondConsumer = new AbortController();
    const queryFn = vi.fn((signal: AbortSignal) => {
      underlyingSignal = signal;
      return work.promise;
    });

    const one = client.query({ key: ["users"], signal: firstConsumer.signal, queryFn });
    const two = client.query({ key: ["users"], signal: secondConsumer.signal, queryFn });
    firstConsumer.abort();
    await expect(one).rejects.toMatchObject({ name: "AbortError" });
    await Promise.resolve();
    expect(underlyingSignal?.aborted).toBe(false);

    work.resolve("done");
    await expect(two).resolves.toBe("done");
  });

  it("invalidates in-flight work and ignores a late signal-insensitive result", async () => {
    const client = new SessionQueryClient();
    const work = deferred<string>();
    const pending = client.query({ key: ["projects", "one"], queryFn: () => work.promise });

    client.invalidate(["projects"]);

    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });

  it("starts a fresh epoch even when the same actor signs in again", async () => {
    const client = new SessionQueryClient();
    const oldWork = deferred<string>();
    const pending = client.query({ key: ["session-data"], queryFn: () => oldWork.promise });

    client.beginSession("user-1");
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    await expect(client.query({
      key: ["session-data"],
      queryFn: async () => "new",
    })).resolves.toBe("new");
  });

  it("resets the session epoch on 401 but keeps 403 local", async () => {
    const client = new SessionQueryClient();
    const invalidated = vi.fn();
    client.onSessionInvalidated(invalidated);

    await expect(client.query({
      key: ["permissions"],
      queryFn: async () => {
        throw new ApiError({ error_code: "access_denied" }, 403);
      },
    })).rejects.toMatchObject({ status: 403 });
    expect(invalidated).not.toHaveBeenCalled();

    await expect(client.query({
      key: ["documents"],
      queryFn: async () => {
        throw new ApiError({ error_code: "unauthenticated" }, 401);
      },
    })).rejects.toMatchObject({ name: "AbortError" });
    expect(invalidated).toHaveBeenCalledTimes(1);
  });
});
