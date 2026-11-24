"use client";

/**
 * Research controls.
 *
 * Analysis selections live in the URL. That is a research requirement, not a
 * convenience: a result is only reproducible if the exact view that produced it
 * can be linked to and reopened.
 */

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import type { ReactNode } from "react";

const selectClass =
  "rounded border border-border bg-panel px-2 py-1.5 text-xs text-ink transition-colors hover:border-border-strong focus:border-accent";

export interface VariableOption {
  name: string;
  label: string;
  availability: string;
  nonNull: number;
  disabled?: boolean;
}

function useUpdateParams() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  return (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params.toString());
    for (const [key, value] of Object.entries(changes)) {
      if (value === null || value === "") next.delete(key);
      else next.set(key, value);
    }
    router.push(`${pathname}?${next.toString()}`);
  };
}

export function ParamSelect({
  paramKey,
  label,
  options,
  defaultValue,
  hint,
}: {
  paramKey: string;
  label: string;
  options: Array<{ value: string; label: string; disabled?: boolean }>;
  defaultValue?: string;
  hint?: string;
}) {
  const params = useSearchParams();
  const update = useUpdateParams();
  const id = `param-${paramKey}`;
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-[10px] font-medium uppercase tracking-wider text-ink-muted">
        {label}
      </label>
      <select
        id={id}
        value={params.get(paramKey) ?? defaultValue ?? ""}
        onChange={(event) => update({ [paramKey]: event.target.value })}
        className={selectClass}
        title={hint}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value} disabled={option.disabled}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  );
}

/**
 * Variable picker that marks each option with its leakage class.
 *
 * Showing "outcome" and "forward-looking" inline is a teaching device: the user
 * can see which variables are legal model inputs before trying to use one.
 */
export function VariableSelect({
  paramKey,
  label,
  variables,
  defaultValue,
}: {
  paramKey: string;
  label: string;
  variables: VariableOption[];
  defaultValue?: string;
}) {
  const marker = (availability: string) =>
    availability === "at_launch"
      ? "known at launch"
      : availability === "outcome"
        ? "outcome"
        : "forward-looking";

  return (
    <ParamSelect
      paramKey={paramKey}
      label={label}
      defaultValue={defaultValue}
      options={variables.map((variable) => ({
        value: variable.name,
        label: `${variable.label} — ${marker(variable.availability)} (n=${variable.nonNull})`,
        disabled: variable.disabled,
      }))}
    />
  );
}

export function ParamToggle({
  paramKey,
  label,
  hint,
}: {
  paramKey: string;
  label: string;
  hint?: string;
}) {
  const params = useSearchParams();
  const update = useUpdateParams();
  const checked = params.get(paramKey) === "true";
  return (
    <label
      className="mt-auto flex items-center gap-1.5 rounded border border-border bg-panel px-2 py-1.5 text-xs text-ink-secondary"
      title={hint}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => update({ [paramKey]: event.target.checked ? "true" : null })}
        className="accent-accent"
      />
      {label}
    </label>
  );
}

export function ControlBar({ children }: { children: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end gap-3 rounded-md border border-border bg-surface px-4 py-3">
      {children}
    </div>
  );
}
