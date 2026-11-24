import { ApiError, SchemaError } from "@/lib/api";
import { ErrorState } from "@/components/ui/primitives";

/**
 * Renders a failed API call with its real cause.
 *
 * The three cases are distinguished because the fix differs: an unreachable
 * backend needs starting, an HTTP error needs reading, and a schema mismatch
 * means the frontend and backend versions have diverged.
 */
export function ApiErrorPanel({ error }: { error: unknown }) {
  if (error instanceof ApiError) {
    return (
      <ErrorState
        title={error.isUnreachable ? "The research API is unreachable" : "The API returned an error"}
        detail={error.detail}
        hint={
          error.isUnreachable ? (
            <div className="space-y-2">
              <p>Start the backend, then reload:</p>
              <pre className="overflow-x-auto rounded border border-border bg-panel px-3 py-2 text-[11px] text-ink-secondary">
                <code>{`cd backend\nuvicorn app.main:app --reload`}</code>
              </pre>
            </div>
          ) : (
            <span className="break-all">Requested: {error.url}</span>
          )
        }
      />
    );
  }
  if (error instanceof SchemaError) {
    return (
      <ErrorState
        title="API response did not match the expected shape"
        detail={error.message}
        hint={
          <ul className="list-inside list-disc space-y-0.5">
            {error.issues.map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        }
      />
    );
  }
  return (
    <ErrorState detail={error instanceof Error ? error.message : "An unexpected error occurred."} />
  );
}
