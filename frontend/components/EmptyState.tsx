import Link from "next/link";

interface EmptyStateProps {
  title: string;
  body?: string;
  hint?: string;
  cta?: { href: string; label: string };
}

/** Graceful empty / backend-down state (no daily yet, archive empty, etc.). */
export function EmptyState({ title, body, hint, cta }: EmptyStateProps): JSX.Element {
  return (
    <section className="my-10 rounded-md border border-dashed border-hairline px-6 py-12 text-center">
      <p className="label-mono mb-3 text-muted">Nothing here yet</p>
      <h2 className="font-serif text-2xl font-semibold">{title}</h2>
      {body ? <p className="mx-auto mt-3 max-w-md leading-body text-ink/80">{body}</p> : null}
      {hint ? (
        <p className="mx-auto mt-4 max-w-md font-mono text-xs leading-body text-muted">{hint}</p>
      ) : null}
      {cta ? (
        <Link
          href={cta.href}
          className="mt-6 inline-block rounded border border-accent px-4 py-2 font-mono text-xs uppercase tracking-[0.12em] text-accent hover:bg-accent/10"
        >
          {cta.label}
        </Link>
      ) : null}
    </section>
  );
}
