import Link from "next/link";
import { ChevronRight } from "lucide-react";

export interface Crumb {
  label: string;
  href?: string;
}

/** Breadcrumbs. The last item is the current page and is not a link. */
export function Breadcrumbs({ items }: { items: Crumb[] }) {
  if (!items.length) return null;
  return (
    <nav aria-label="Breadcrumb" className="mb-4">
      <ol className="flex flex-wrap items-center gap-1 text-[11px] text-ink-muted">
        {items.map((item, index) => {
          const last = index === items.length - 1;
          return (
            <li key={`${item.label}-${index}`} className="flex items-center gap-1">
              {item.href && !last ? (
                <Link href={item.href} className="transition-colors hover:text-ink-secondary">
                  {item.label}
                </Link>
              ) : (
                <span className={last ? "text-ink-secondary" : undefined} aria-current={last ? "page" : undefined}>
                  {item.label}
                </span>
              )}
              {!last ? <ChevronRight size={11} aria-hidden="true" className="opacity-60" /> : null}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
