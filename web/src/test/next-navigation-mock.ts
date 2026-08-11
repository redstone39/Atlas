import { useSyncExternalStore } from "react";

const listeners = new Set<() => void>();

function notifyNavigation() {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  window.addEventListener("popstate", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("popstate", listener);
  };
}

export function usePathname() {
  return useSyncExternalStore(
    subscribe,
    () => window.location.pathname,
    () => "/login",
  );
}

export function useRouter() {
  return {
    push(destination: string) {
      window.history.pushState({}, "", destination);
      notifyNavigation();
    },
    replace(destination: string) {
      window.history.replaceState({}, "", destination);
      notifyNavigation();
    },
    back() {
      window.history.back();
    },
    forward() {
      window.history.forward();
    },
    refresh() {
      notifyNavigation();
    },
    prefetch: async () => undefined,
  };
}
