"use client";

/**
 * A boolean preference persisted in localStorage, read with
 * `useSyncExternalStore`.
 *
 * Why not `useState` + an effect: reading storage inside an effect and calling
 * setState causes a cascading render on every mount, and initialising state
 * directly from localStorage breaks server rendering (the server has no such
 * API, so the markup would not match). `useSyncExternalStore` solves both: it
 * takes an explicit server snapshot and subscribes to changes.
 *
 * Every storage access is wrapped: in a private window, or with site data
 * blocked, these throw rather than returning null.
 */

import { useCallback, useSyncExternalStore } from "react";

const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

function subscribe(onStoreChange: () => void) {
  listeners.add(onStoreChange);
  // Keep tabs in step when the value changes elsewhere.
  window.addEventListener("storage", onStoreChange);
  return () => {
    listeners.delete(onStoreChange);
    window.removeEventListener("storage", onStoreChange);
  };
}

function read(key: string): boolean {
  try {
    return window.localStorage.getItem(key) === "1";
  } catch {
    return false;
  }
}

function write(key: string, value: boolean): void {
  try {
    window.localStorage.setItem(key, value ? "1" : "0");
  } catch {
    /* storage unavailable: the preference simply does not persist */
  }
}

export function usePersistedFlag(key: string): [boolean, (value: boolean) => void] {
  const value = useSyncExternalStore(
    subscribe,
    () => read(key),
    // Server snapshot: the default, so SSR and the first client render agree.
    () => false,
  );

  const setValue = useCallback(
    (next: boolean) => {
      write(key, next);
      emit();
    },
    [key],
  );

  return [value, setValue];
}
