"use client";

import { ErrorState } from "@/components/ui/primitives";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="mx-auto max-w-2xl py-10">
      <ErrorState
        title="Something went wrong rendering this page"
        detail={error.message || "An unexpected error occurred."}
        hint={
          <button
            type="button"
            onClick={reset}
            className="mt-1 rounded border border-border px-3 py-1.5 text-xs text-ink-secondary transition-colors hover:border-border-strong hover:text-ink"
          >
            Try again
          </button>
        }
      />
    </div>
  );
}
