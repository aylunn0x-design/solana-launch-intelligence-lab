"use client";

/**
 * Collapsible sidebar navigation.
 *
 * Collapse state is kept in localStorage so the layout survives navigation,
 * wrapped in try/catch because storage throws in private windows. On small
 * screens it becomes an off-canvas drawer with a focusable close control.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { usePersistedFlag } from "@/lib/use-persisted-flag";
import {
  Activity,
  Beaker,
  ChevronLeft,
  Database,
  FlaskConical,
  LayoutDashboard,
  Menu,
  ScrollText,
  Settings,
  Table,
  X,
} from "lucide-react";

const NAV = [
  {
    section: "Data",
    items: [
      { href: "/overview", label: "Overview", Icon: LayoutDashboard },
      { href: "/explorer", label: "Token Explorer", Icon: Table },
      { href: "/data-status", label: "Data Status", Icon: Database },
    ],
  },
  {
    section: "Research",
    items: [
      { href: "/research", label: "Research Lab", Icon: FlaskConical },
      { href: "/research/correlation", label: "Correlation", Icon: Activity },
      { href: "/research/survival", label: "Survival", Icon: Activity },
      { href: "/experiments", label: "Experiments", Icon: Beaker },
    ],
  },
  {
    section: "Reference",
    items: [
      { href: "/methodology", label: "Methodology", Icon: ScrollText },
      { href: "/settings", label: "Settings", Icon: Settings },
    ],
  },
] as const;

const STORAGE_KEY = "slil.sidebar.collapsed";

export function Sidebar() {
  const pathname = usePathname();
  // Read through an external store rather than an effect: no cascading render
  // on mount, and server rendering gets an explicit default.
  const [collapsed, setCollapsed] = usePersistedFlag(STORAGE_KEY);
  const [mobileOpen, setMobileOpen] = useState(false);

  // Close the drawer when the route changes. Adjusting state during render on a
  // changed input is the documented React pattern; doing it in an effect would
  // render the stale open drawer first, then immediately re-render it closed.
  const [lastPathname, setLastPathname] = useState(pathname);
  if (pathname !== lastPathname) {
    setLastPathname(pathname);
    if (mobileOpen) setMobileOpen(false);
  }

  // Escape closes the drawer.
  useEffect(() => {
    if (!mobileOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMobileOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mobileOpen]);

  const isActive = (href: string) =>
    pathname === href ||
    (href !== "/overview" && pathname.startsWith(`${href}/`)) ||
    (href === "/research" && pathname === "/research");

  const width = collapsed ? "lg:w-[60px]" : "lg:w-[228px]";

  return (
    <>
      {/* Mobile top bar */}
      <div className="sticky top-0 z-40 flex items-center gap-3 border-b border-border bg-surface px-4 py-2.5 lg:hidden">
        <button
          type="button"
          onClick={() => setMobileOpen(true)}
          aria-label="Open navigation"
          aria-expanded={mobileOpen}
          className="rounded border border-border p-1.5 text-ink-secondary"
        >
          <Menu size={16} aria-hidden="true" />
        </button>
        <span className="truncate text-[13px] font-semibold tracking-tight">
          Solana Launch Intelligence Lab
        </span>
      </div>

      {/* Backdrop */}
      {mobileOpen ? (
        <div
          className="fixed inset-0 z-40 bg-black/60 lg:hidden"
          onClick={() => setMobileOpen(false)}
          aria-hidden="true"
        />
      ) : null}

      <nav
        aria-label="Main navigation"
        className={`fixed inset-y-0 left-0 z-50 flex w-[260px] shrink-0 flex-col border-r border-border bg-surface transition-transform duration-150 lg:sticky lg:top-0 lg:h-screen lg:translate-x-0 lg:transition-[width] ${width} ${
          mobileOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex items-center gap-2 border-b border-border px-3 py-3">
          {!collapsed ? (
            <Link
              href="/overview"
              className="min-w-0 flex-1 text-[13px] font-semibold leading-tight tracking-tight text-ink"
            >
              Solana Launch
              <span className="block text-ink-muted">Intelligence Lab</span>
            </Link>
          ) : null}
          <button
            type="button"
            onClick={() => setCollapsed(!collapsed)}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            className="ml-auto hidden rounded p-1 text-ink-muted transition-colors hover:bg-raised hover:text-ink-secondary lg:block"
          >
            <ChevronLeft
              size={15}
              aria-hidden="true"
              className={`transition-transform ${collapsed ? "rotate-180" : ""}`}
            />
          </button>
          <button
            type="button"
            onClick={() => setMobileOpen(false)}
            aria-label="Close navigation"
            className="ml-auto rounded p-1 text-ink-muted lg:hidden"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-2 py-3">
          {NAV.map((group) => (
            <div key={group.section} className="mb-4">
              {!collapsed ? (
                <div className="px-2 pb-1.5 text-[10px] font-semibold uppercase tracking-wider text-ink-muted">
                  {group.section}
                </div>
              ) : null}
              <ul className="space-y-0.5">
                {group.items.map(({ href, label, Icon }) => {
                  const active = isActive(href);
                  return (
                    <li key={href}>
                      <Link
                        href={href}
                        aria-current={active ? "page" : undefined}
                        title={collapsed ? label : undefined}
                        className={`flex items-center gap-2.5 rounded px-2 py-1.5 text-[13px] transition-colors ${
                          active
                            ? "bg-raised font-medium text-ink"
                            : "text-ink-secondary hover:bg-raised/60 hover:text-ink"
                        } ${collapsed ? "justify-center" : ""}`}
                      >
                        <Icon
                          size={15}
                          aria-hidden="true"
                          className={active ? "text-accent" : "text-ink-muted"}
                        />
                        {!collapsed ? <span className="truncate">{label}</span> : null}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>

        {!collapsed ? (
          <div className="border-t border-border px-3 py-3 text-[10px] leading-relaxed text-ink-muted">
            Research instrument. Associations from observational data — not
            financial advice, and not a trading system.
          </div>
        ) : null}
      </nav>
    </>
  );
}
