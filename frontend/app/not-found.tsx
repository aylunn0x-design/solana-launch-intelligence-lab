import Link from "next/link";
import { EmptyState } from "@/components/ui/primitives";

export default function NotFound() {
  return (
    <EmptyState
      title="Page not found"
      description="That route does not exist in this application."
      action={
        <Link
          href="/overview"
          className="rounded border border-border px-3 py-1.5 text-xs text-ink-secondary transition-colors hover:border-border-strong hover:text-ink"
        >
          Back to Overview
        </Link>
      }
    />
  );
}
