"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

interface NavItem {
  href: string;
  label: string;
  match: (path: string) => boolean;
}

const NAV: NavItem[] = [
  { href: "/", label: "Today", match: (p) => p === "/" },
  { href: "/week", label: "Week at a Glance", match: (p) => p.startsWith("/week") },
  { href: "/archive", label: "Archive", match: (p) => p.startsWith("/archive") },
];

export function SiteNav(): JSX.Element {
  const pathname = usePathname() || "/";

  return (
    <header className="border-b border-hairline bg-paper/90 backdrop-blur supports-[backdrop-filter]:bg-paper/70">
      <div className="mx-auto flex w-full max-w-wide flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <Link href="/" className="group flex items-baseline gap-2" aria-label="ai-digest home">
          <span className="font-serif text-xl font-bold tracking-tight">ai-digest</span>
          <span className="label-mono hidden sm:inline">a smol.ai for one</span>
        </Link>

        <nav aria-label="Primary">
          <ul className="flex items-center gap-1 sm:gap-2">
            {NAV.map((item) => {
              const active = item.match(pathname);
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    aria-current={active ? "page" : undefined}
                    className={[
                      "rounded px-2.5 py-1.5 font-mono text-xs uppercase tracking-[0.12em] transition-colors",
                      active
                        ? "bg-accent/10 text-accent"
                        : "text-muted hover:text-ink",
                    ].join(" ")}
                  >
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      </div>
    </header>
  );
}
