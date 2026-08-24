export function clientRequestId(scope: string) {
  const entropy =
    globalThis.crypto?.randomUUID?.().replace(/-/g, "") ??
    Array.from(
      globalThis.crypto?.getRandomValues?.(new Uint8Array(16)) ?? [
        Date.now() & 0xff,
        Math.floor(Math.random() * 256),
      ],
      (byte) => byte.toString(16).padStart(2, "0"),
    ).join("");
  return `${scope}-${entropy}`;
}

export type ClientOperationKey = {
  fingerprint: string;
  idempotencyKey: string;
};

export function retainClientRequestId(
  previous: ClientOperationKey | null,
  scope: string,
  fingerprint: string,
): ClientOperationKey {
  if (previous?.fingerprint === fingerprint) return previous;
  return {
    fingerprint,
    idempotencyKey: clientRequestId(scope),
  };
}
