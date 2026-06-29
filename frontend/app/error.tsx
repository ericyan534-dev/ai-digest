"use client";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}): JSX.Element {
  return (
    <section className="my-12 rounded-md border border-dashed border-hairline px-6 py-12 text-center">
      <p className="label-mono mb-3 text-accent">Something broke</p>
      <h1 className="font-serif text-2xl font-semibold">Could not render this view</h1>
      <p className="mx-auto mt-3 max-w-md leading-body text-muted">
        The page hit an unexpected error. This is usually a transient backend hiccup.
      </p>
      <button
        type="button"
        onClick={reset}
        className="mt-6 inline-block rounded border border-accent px-4 py-2 font-mono text-xs uppercase tracking-[0.12em] text-accent hover:bg-accent/10"
      >
        Try again
      </button>
    </section>
  );
}
