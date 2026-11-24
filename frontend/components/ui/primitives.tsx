/**
 * Design-system primitives.
 *
 * Every page is built from these so spacing, borders and type stay consistent.
 * Nothing here is decorative: each component exists because several pages need
 * the same behaviour (an empty state, a warning banner, a metric tile).
 */

import type { ReactNode } from "react";
import {
  AlertTriangle,
  CircleAlert,
  CircleCheck,
  Info,
  MinusCircle,
} from "lucide-react";

/* -------------------------------------------------------------------------- */
/* Layout                                                                     */
/* -------------------------------------------------------------------------- */

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-6 flex flex-col gap-3 border-b border-border pb-5 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight text-ink">{title}</h1>
        {description ? (
          <p className="mt-1.5 max-w-3xl text-[13px] leading-relaxed text-ink-secondary">
            {description}
          </p>
        ) : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </header>
  );
}

export function Panel({
  title,
  description,
  actions,
  children,
  className = "",
  bodyClassName = "",
}: {
  title?: string;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section
      // min-w-0: a grid/flex item defaults to min-width:auto, which stops it
      // shrinking below its content. Without this a panel holding a wide table
      // forces the whole page wider than the viewport on narrow screens, and
      // the table's own overflow-x-auto never gets a chance to scroll.
      className={`min-w-0 rounded-md border border-border bg-surface ${className}`}
    >
      {title ? (
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-4 py-3">
          <div className="min-w-0">
            <h2 className="text-[13px] font-semibold uppercase tracking-wider text-ink-secondary">
              {title}
            </h2>
            {description ? (
              <p className="mt-1 max-w-2xl text-xs leading-relaxed text-ink-muted">
                {description}
              </p>
            ) : null}
          </div>
          {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
        </div>
      ) : null}
      <div className={`p-4 ${bodyClassName}`}>{children}</div>
    </section>
  );
}

/* -------------------------------------------------------------------------- */
/* Metric display                                                             */
/* -------------------------------------------------------------------------- */

/**
 * A single number with its label and optional context.
 *
 * `hint` is where the metric's definition goes — unfamiliar research metrics
 * must be explainable in place, not only in the methodology page.
 */
export function Metric({
  label,
  value,
  hint,
  sub,
  tone = "neutral",
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  sub?: ReactNode;
  tone?: "neutral" | "up" | "down";
}) {
  const toneClass =
    tone === "up" ? "text-up" : tone === "down" ? "text-down" : "text-ink";
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-1.5">
        <span className="text-[11px] font-medium uppercase tracking-wider text-ink-muted">
          {label}
        </span>
        {hint ? <Tooltip text={hint} /> : null}
      </div>
      <div className={`tnum mt-1 truncate text-[22px] font-semibold leading-tight ${toneClass}`}>
        {value}
      </div>
      {sub ? <div className="mt-0.5 text-xs text-ink-muted">{sub}</div> : null}
    </div>
  );
}

export function MetricGrid({ children }: { children: ReactNode }) {
  return (
    <div className="grid grid-cols-2 gap-x-6 gap-y-5 sm:grid-cols-3 lg:grid-cols-4">
      {children}
    </div>
  );
}

/**
 * A keyboard-accessible tooltip.
 *
 * Implemented as a <button> with aria-describedby rather than a hover-only
 * title, so the explanation is reachable by keyboard and announced by screen
 * readers. Shown on hover and on focus.
 *
 * Positioning differs by screen size on purpose. A 256px panel anchored to a
 * 16px trigger overflows the viewport on a phone — the text ends up partly
 * off-screen and unreadable, and it widens the page. On small screens it is
 * therefore pinned above the bottom edge, spanning the available width, and
 * only becomes an anchored popover from `sm` up.
 *
 * From `sm` it is right-aligned to the trigger rather than centred on it.
 * A centred popover always overhangs by half its width, so any trigger near a
 * right-hand edge pushes the page wider at every viewport size. Growing
 * leftward cannot: overflow past the left edge does not create a scrollbar.
 */
export function Tooltip({ text, label = "What is this?" }: { text: string; label?: string }) {
  const id = `tip-${text.slice(0, 24).replace(/\W+/g, "-").toLowerCase()}`;
  return (
    <span className="group relative inline-flex">
      <button
        type="button"
        aria-label={label}
        aria-describedby={id}
        className="inline-flex h-4 w-4 items-center justify-center rounded-full border border-border-strong text-[9px] font-bold text-ink-muted transition-colors hover:border-ink-muted hover:text-ink-secondary"
      >
        ?
      </button>
      <span
        role="tooltip"
        id={id}
        className="pointer-events-none fixed inset-x-4 bottom-4 z-30 rounded-md border border-border-strong bg-raised px-3 py-2 text-xs font-normal normal-case leading-relaxed tracking-normal text-ink-secondary opacity-0 shadow-lg transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 sm:absolute sm:inset-x-auto sm:bottom-auto sm:right-0 sm:top-full sm:mt-1.5 sm:w-64"
      >
        {text}
      </span>
    </span>
  );
}

/* -------------------------------------------------------------------------- */
/* States: loading / empty / error                                            */
/* -------------------------------------------------------------------------- */

export function Skeleton({ className = "h-4 w-full" }: { className?: string }) {
  return <div className={`skeleton ${className}`} aria-hidden="true" />;
}

export function SkeletonPanel({ rows = 4, title }: { rows?: number; title?: string }) {
  return (
    <Panel title={title}>
      <div className="space-y-3" role="status" aria-label="Loading">
        {Array.from({ length: rows }).map((_, i) => (
          <Skeleton key={i} className={`h-4 ${i === 0 ? "w-1/3" : "w-full"}`} />
        ))}
        <span className="sr-only">Loading…</span>
      </div>
    </Panel>
  );
}

export function SkeletonChart({ height = 260 }: { height?: number }) {
  return (
    <div
      className="skeleton w-full rounded"
      style={{ height }}
      role="status"
      aria-label="Loading chart"
    />
  );
}

/** An explicit empty state. Always says what to do next. */
export function EmptyState({
  title,
  description,
  action,
  icon,
}: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-12 text-center">
      <div className="text-ink-muted">{icon ?? <MinusCircle size={22} strokeWidth={1.5} />}</div>
      <h3 className="text-sm font-semibold text-ink">{title}</h3>
      {description ? (
        // w-full + min-w-0: this sits in a flex column, where a child defaults
        // to min-width:auto and so refuses to shrink below the width of a long
        // <pre>. Without these a code sample in an empty state widens the page.
        <div className="w-full min-w-0 max-w-xl text-[13px] leading-relaxed text-ink-secondary">
          {description}
        </div>
      ) : null}
      {action ? <div className="mt-1">{action}</div> : null}
    </div>
  );
}

/** An error state that shows the real reason. Never a generic "oops". */
export function ErrorState({
  title = "Could not load this data",
  detail,
  hint,
}: {
  title?: string;
  detail: string;
  hint?: ReactNode;
}) {
  return (
    <div
      role="alert"
      className="flex flex-col gap-2 rounded-md border border-status-critical/35 bg-status-critical/[0.07] px-4 py-3.5"
    >
      <div className="flex items-center gap-2">
        <CircleAlert size={15} className="shrink-0 text-status-critical" aria-hidden="true" />
        <h3 className="text-[13px] font-semibold text-ink">{title}</h3>
      </div>
      <p className="break-words text-xs leading-relaxed text-ink-secondary">{detail}</p>
      {hint ? <div className="text-xs leading-relaxed text-ink-muted">{hint}</div> : null}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Warnings and notes                                                         */
/* -------------------------------------------------------------------------- */

export type Severity = "info" | "low" | "medium" | "high" | "critical";

const SEVERITY_STYLE: Record<Severity, { border: string; bg: string; fg: string }> = {
  info: { border: "border-border-strong", bg: "bg-raised/60", fg: "text-ink-muted" },
  low: { border: "border-border-strong", bg: "bg-raised/60", fg: "text-ink-muted" },
  medium: {
    border: "border-status-warning/35",
    bg: "bg-status-warning/[0.07]",
    fg: "text-status-warning",
  },
  high: {
    border: "border-status-serious/40",
    bg: "bg-status-serious/[0.08]",
    fg: "text-status-serious",
  },
  critical: {
    border: "border-status-critical/40",
    bg: "bg-status-critical/[0.08]",
    fg: "text-status-critical",
  },
};

/**
 * A methodological caveat.
 *
 * These are never collapsed away by default: on this platform the limitations
 * are part of the result, not a footnote. Status colour is always paired with
 * an icon and a text label so it never relies on hue alone.
 */
export function Callout({
  severity = "info",
  title,
  children,
}: {
  severity?: Severity;
  title?: string;
  children: ReactNode;
}) {
  const style = SEVERITY_STYLE[severity];
  const Icon =
    severity === "info" || severity === "low"
      ? Info
      : severity === "medium"
        ? AlertTriangle
        : CircleAlert;
  return (
    <div className={`rounded-md border ${style.border} ${style.bg} px-3.5 py-3`}>
      <div className="flex gap-2.5">
        <Icon size={14} className={`mt-0.5 shrink-0 ${style.fg}`} aria-hidden="true" />
        <div className="min-w-0 flex-1">
          {title ? (
            <div className={`text-[11px] font-bold uppercase tracking-wider ${style.fg}`}>
              {title}
            </div>
          ) : null}
          <div
            className={`text-xs leading-relaxed text-ink-secondary ${title ? "mt-1" : ""}`}
          >
            {children}
          </div>
        </div>
      </div>
    </div>
  );
}

/** Renders the warnings every statistical result carries. */
export function WarningList({
  warnings,
  title = "Interpretation & limitations",
}: {
  warnings: string[];
  title?: string;
}) {
  if (!warnings.length) return null;
  // A warning naming a bias is escalated so it cannot read as boilerplate.
  const severity: Severity = warnings.some(
    (w) => w.includes("SURVIVORSHIP") || w.includes("not supportable"),
  )
    ? "high"
    : "medium";
  return (
    <Callout severity={severity} title={title}>
      <ul className="space-y-1.5">
        {warnings.map((warning, i) => (
          <li key={i} className="flex gap-2">
            <span aria-hidden="true" className="select-none text-ink-muted">
              •
            </span>
            <span>{warning}</span>
          </li>
        ))}
      </ul>
    </Callout>
  );
}

/* -------------------------------------------------------------------------- */
/* Small bits                                                                 */
/* -------------------------------------------------------------------------- */

export function Badge({
  children,
  tone = "neutral",
  title,
}: {
  children: ReactNode;
  tone?: "neutral" | "good" | "warning" | "critical" | "accent";
  title?: string;
}) {
  const tones = {
    neutral: "border-border-strong text-ink-muted",
    good: "border-status-good/40 text-status-good",
    warning: "border-status-warning/40 text-status-warning",
    critical: "border-status-critical/40 text-status-critical",
    accent: "border-accent/40 text-accent",
  } as const;
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

/** Pass/warn/fail/not-run indicator. Icon + word, never colour alone. */
export function StatusPill({ status }: { status: string }) {
  const map: Record<string, { tone: Parameters<typeof Badge>[0]["tone"]; Icon: typeof CircleCheck }> =
    {
      pass: { tone: "good", Icon: CircleCheck },
      ok: { tone: "good", Icon: CircleCheck },
      succeeded: { tone: "good", Icon: CircleCheck },
      warn: { tone: "warning", Icon: AlertTriangle },
      warnings: { tone: "warning", Icon: AlertTriangle },
      partial: { tone: "warning", Icon: AlertTriangle },
      fail: { tone: "critical", Icon: CircleAlert },
      failed: { tone: "critical", Icon: CircleAlert },
      degraded: { tone: "critical", Icon: CircleAlert },
      not_run: { tone: "neutral", Icon: MinusCircle },
      pending: { tone: "neutral", Icon: MinusCircle },
      running: { tone: "accent", Icon: Info },
    };
  const entry = map[status.toLowerCase()] ?? { tone: "neutral" as const, Icon: Info };
  const { tone, Icon } = entry;
  return (
    <Badge tone={tone}>
      <Icon size={10} aria-hidden="true" />
      {status.replace(/_/g, " ")}
    </Badge>
  );
}

export function KeyValue({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <div className="flex flex-col gap-0.5 border-b border-border/60 py-2 last:border-0 sm:flex-row sm:items-baseline sm:gap-4">
      <dt className="flex shrink-0 items-center gap-1.5 text-xs text-ink-muted sm:w-52">
        {label}
        {hint ? <Tooltip text={hint} /> : null}
      </dt>
      <dd className="min-w-0 break-words text-[13px] text-ink">{children}</dd>
    </div>
  );
}

/** Monospace address with the full value available on hover and to AT. */
export function Address({ value, chars = 5 }: { value: string; chars?: number }) {
  const short =
    value.length <= chars * 2 + 3
      ? value
      : `${value.slice(0, chars)}…${value.slice(-chars)}`;
  return (
    <span className="tnum text-[12px] text-ink-secondary" title={value}>
      <span className="sr-only">{value}</span>
      <span aria-hidden="true">{short}</span>
    </span>
  );
}

export function SectionNote({ children }: { children: ReactNode }) {
  return <p className="mt-3 text-xs leading-relaxed text-ink-muted">{children}</p>;
}
