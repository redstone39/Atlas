export interface ShutdownCarrier {
  destroy(): Promise<void>;
}

export interface ProcessShutdown {
  remove(): void;
  shutdown(): Promise<void>;
}

const SIGNALS = ["SIGINT", "SIGQUIT", "SIGTERM"] as const;
type ShutdownSignal = (typeof SIGNALS)[number];

export function installProcessShutdown(carrier: ShutdownCarrier): ProcessShutdown {
  let shutdownPromise: Promise<void> | null = null;

  function remove(): void {
    for (const signal of SIGNALS) {
      process.off(signal, signalHandlers[signal]);
    }
  }

  function shutdown(): Promise<void> {
    if (shutdownPromise) return shutdownPromise;
    shutdownPromise = carrier.destroy()
      .catch(error => {
        process.exitCode = 1;
        console.error("Collaboration carrier shutdown failed", error);
      })
      .finally(remove);
    return shutdownPromise;
  }

  const signalHandlers = {
    SIGINT: () => void shutdown(),
    SIGQUIT: () => void shutdown(),
    SIGTERM: () => void shutdown(),
  } satisfies Record<ShutdownSignal, () => void>;

  for (const signal of SIGNALS) {
    process.once(signal, signalHandlers[signal]);
  }
  return { remove, shutdown };
}
