export default function Loading(): JSX.Element {
  return (
    <div className="mx-auto max-w-editorial animate-pulse">
      <div className="mb-8 border-b border-hairline pb-6">
        <div className="mb-3 h-3 w-40 rounded bg-hairline" />
        <div className="h-8 w-3/4 rounded bg-hairline" />
      </div>
      {[0, 1, 2].map((i) => (
        <div key={i} className="border-b border-hairline py-6">
          <div className="mb-2 h-3 w-24 rounded bg-hairline" />
          <div className="mb-2 h-5 w-2/3 rounded bg-hairline" />
          <div className="h-3 w-full rounded bg-hairline" />
          <div className="mt-2 h-3 w-5/6 rounded bg-hairline" />
        </div>
      ))}
      <span className="sr-only">Loading…</span>
    </div>
  );
}
