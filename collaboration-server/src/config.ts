export interface CarrierConfig {
  readonly apiBaseUrl: string;
  readonly internalSecret: string;
  readonly host: string;
  readonly port: number;
  readonly requestTimeoutMs: number;
}

function positiveInteger(name: string, value: string | undefined, fallback: number): number {
  if (value === undefined) return fallback;
  if (!/^[1-9]\d*$/.test(value)) throw new Error(`${name} must be a positive integer`);
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed)) throw new Error(`${name} is outside the safe integer range`);
  return parsed;
}

export function readConfig(environment: NodeJS.ProcessEnv = process.env): CarrierConfig {
  const rawUrl = environment.ATLAS_NOTES_COLLABORATION_INTERNAL_URL;
  const internalSecret = environment.ATLAS_NOTES_COLLABORATION_INTERNAL_SECRET;
  if (!rawUrl) throw new Error("ATLAS_NOTES_COLLABORATION_INTERNAL_URL is required");
  if (!internalSecret) throw new Error("ATLAS_NOTES_COLLABORATION_INTERNAL_SECRET is required");

  const url = new URL(rawUrl);
  if ((url.protocol !== "http:" && url.protocol !== "https:") || url.username || url.password || url.search || url.hash) {
    throw new Error("ATLAS_NOTES_COLLABORATION_INTERNAL_URL must be an HTTP(S) base URL without credentials, query, or fragment");
  }

  const port = positiveInteger("ATLAS_NOTES_COLLABORATION_PORT", environment.ATLAS_NOTES_COLLABORATION_PORT, 8015);
  if (port > 65_535) throw new Error("ATLAS_NOTES_COLLABORATION_PORT must be at most 65535");

  return Object.freeze({
    apiBaseUrl: rawUrl.replace(/\/$/, ""),
    internalSecret,
    host: environment.ATLAS_NOTES_COLLABORATION_HOST || "127.0.0.1",
    port,
    requestTimeoutMs: positiveInteger("ATLAS_NOTES_COLLABORATION_REQUEST_TIMEOUT_MS", environment.ATLAS_NOTES_COLLABORATION_REQUEST_TIMEOUT_MS, 5_000),
  });
}
