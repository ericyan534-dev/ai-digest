interface QuietDayNoticeProps {
  /** The honest TL;DR from the backend, e.g. "Quiet day — nothing major shipped." */
  message?: string;
  variant?: "day" | "week";
}

/**
 * Honest quiet-day / quiet-week state (the flexibility principle, surfaced).
 * No manufactured importance — a calm, plain notice.
 */
export function QuietDayNotice({
  message,
  variant = "day",
}: QuietDayNoticeProps): JSX.Element {
  const fallback =
    variant === "week"
      ? "Quiet week — nothing major shipped."
      : "Quiet day — nothing major shipped.";
  return (
    <section
      aria-label={variant === "week" ? "Quiet week" : "Quiet day"}
      className="my-6 rounded-md border border-dashed border-hairline bg-paper px-5 py-8 text-center"
    >
      <p className="label-mono mb-2 text-muted">{variant === "week" ? "Quiet week" : "Quiet day"}</p>
      <p className="font-serif text-xl italic text-ink/80">{message || fallback}</p>
      <p className="mx-auto mt-3 max-w-md text-sm leading-body text-muted">
        We won&apos;t manufacture importance. When something real ships, it&apos;ll be here in full
        depth.
      </p>
    </section>
  );
}
