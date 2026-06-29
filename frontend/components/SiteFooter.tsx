import { API_BASE } from "@/lib/api";

export function SiteFooter(): JSX.Element {
  return (
    <footer className="border-t border-hairline">
      <div className="mx-auto flex w-full max-w-wide flex-col gap-1 px-4 py-6 sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <p className="label-mono">ai-digest · self-hosted · single-user</p>
        <p className="label-mono">
          api: <span className="text-ink/70">{API_BASE}</span>
        </p>
      </div>
    </footer>
  );
}
