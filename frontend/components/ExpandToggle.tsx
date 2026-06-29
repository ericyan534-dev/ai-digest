"use client";

import { useId, useState, type ReactNode } from "react";

interface ExpandToggleProps {
  /** Label for the collapsed affordance, e.g. "Full depth". */
  label?: string;
  collapsedLabel?: string;
  expandedLabel?: string;
  defaultOpen?: boolean;
  children: ReactNode;
}

/**
 * Accessible expand/collapse for full-depth (BREAKTHROUGH) story context.
 * Button controls a region via aria-expanded / aria-controls.
 */
export function ExpandToggle({
  collapsedLabel = "Read full depth",
  expandedLabel = "Show less",
  defaultOpen = false,
  children,
}: ExpandToggleProps): JSX.Element {
  const [open, setOpen] = useState(defaultOpen);
  const regionId = useId();

  return (
    <div className="mt-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={regionId}
        className="label-mono inline-flex items-center gap-1.5 text-accent hover:underline"
      >
        <span aria-hidden className="transition-transform" style={{ transform: open ? "rotate(90deg)" : "none" }}>
          ▸
        </span>
        {open ? expandedLabel : collapsedLabel}
      </button>
      <div id={regionId} hidden={!open} className="animate-fade-in">
        {open ? children : null}
      </div>
    </div>
  );
}
