import { ApiError } from "./user-messages";

export type QueryKey = readonly string[];

type QueryOptions<T> = {
  key: QueryKey;
  signal?: AbortSignal;
  queryFn: (signal: AbortSignal) => Promise<T>;
};

type InFlightEntry<T = unknown> = {
  controller: AbortController;
  consumers: Set<symbol>;
  epoch: number;
  valid: boolean;
  promise: Promise<T>;
};

function abortError() {
  return new DOMException("Request was superseded", "AbortError");
}

function serializedKey(key: QueryKey) {
  return JSON.stringify(key);
}

export class SessionQueryClient {
  private epoch = 0;
  private inFlight = new Map<string, InFlightEntry>();
  private sessionInvalidationListeners = new Set<() => void>();

  onSessionInvalidated(listener: () => void) {
    this.sessionInvalidationListeners.add(listener);
    return () => {
      this.sessionInvalidationListeners.delete(listener);
    };
  }

  beginSession(_actorKey: string) {
    this.resetSession();
  }

  resetSession() {
    this.epoch += 1;
    for (const entry of this.inFlight.values()) {
      entry.valid = false;
      entry.controller.abort();
    }
    this.inFlight.clear();
  }

  invalidate(...prefixes: QueryKey[]) {
    for (const [key, entry] of this.inFlight) {
      const parsed = JSON.parse(key) as string[];
      if (prefixes.some((prefix) =>
        prefix.every((part, index) => parsed[index] === part)
      )) {
        entry.valid = false;
        entry.controller.abort();
        this.inFlight.delete(key);
      }
    }
  }

  query<T>({ key, signal, queryFn }: QueryOptions<T>): Promise<T> {
    const mapKey = serializedKey(key);
    const currentEpoch = this.epoch;
    let entry = this.inFlight.get(mapKey) as InFlightEntry<T> | undefined;
    if (!entry || !entry.valid || entry.epoch !== currentEpoch) {
      const controller = new AbortController();
      const created: InFlightEntry<T> = {
        controller,
        consumers: new Set(),
        epoch: currentEpoch,
        valid: true,
        promise: Promise.resolve()
          .then(() => queryFn(controller.signal))
          .catch((error) => {
            this.handleApiError(error, key);
            throw error;
          }),
      };
      entry = created;
      this.inFlight.set(mapKey, created as InFlightEntry);
      created.promise.then(
        () => this.removeSettled(mapKey, created),
        () => this.removeSettled(mapKey, created),
      );
    }

    const consumer = Symbol(mapKey);
    entry.consumers.add(consumer);
    return new Promise<T>((resolve, reject) => {
      let settled = false;
      const underlyingSignal = entry.controller.signal;
      const cleanupAbortListeners = () => {
        signal?.removeEventListener("abort", rejectAbort);
        underlyingSignal.removeEventListener("abort", rejectAbort);
      };
      const release = () => {
        entry?.consumers.delete(consumer);
        queueMicrotask(() => {
          if (entry?.valid && entry.consumers.size === 0) {
            entry.valid = false;
            entry.controller.abort();
            if (this.inFlight.get(mapKey) === entry) this.inFlight.delete(mapKey);
          }
        });
      };
      const rejectAbort = () => {
        if (settled) return;
        settled = true;
        cleanupAbortListeners();
        release();
        reject(abortError());
      };
      if (signal?.aborted || underlyingSignal.aborted) {
        rejectAbort();
        return;
      }
      signal?.addEventListener("abort", rejectAbort, { once: true });
      underlyingSignal.addEventListener("abort", rejectAbort, { once: true });
      entry.promise.then(
        (value) => {
          if (settled) return;
          if (!entry?.valid || entry.epoch !== this.epoch) {
            rejectAbort();
            return;
          }
          settled = true;
          cleanupAbortListeners();
          release();
          resolve(value);
        },
        (error) => {
          if (settled) return;
          settled = true;
          cleanupAbortListeners();
          release();
          reject(error);
        },
      );
    });
  }

  private removeSettled(key: string, entry: InFlightEntry) {
    if (this.inFlight.get(key) === entry) this.inFlight.delete(key);
  }

  private handleApiError(error: unknown, key: QueryKey) {
    if (!(error instanceof ApiError)) return;
    if (
      error.status === 401
      || error.errorCode === "unauthenticated"
      || error.errorCode === "session_invalid"
    ) {
      this.resetSession();
      for (const listener of this.sessionInvalidationListeners) listener();
      return;
    }
    if (
      error.status === 403
      || error.status === 404
      || error.errorCode === "access_denied"
      || error.errorCode === "not_found"
    ) {
      this.inFlight.delete(serializedKey(key));
    }
  }
}

export function isAbortError(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}

export const sessionQueryClient = new SessionQueryClient();
