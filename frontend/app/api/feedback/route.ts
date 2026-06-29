/**
 * Same-origin proxy for POST /api/feedback.
 *
 * The browser posts here (same origin as the web app); this server-side handler
 * forwards to the FastAPI backend and injects the optional `AIDIGEST_API_KEY`
 * from a SERVER-ONLY env var, so the key is never exposed to the browser.
 */

import { NextRequest, NextResponse } from "next/server";

const API_BASE: string =
  process.env.API_BASE ||
  process.env.NEXT_PUBLIC_API_BASE ||
  process.env.NEXT_PUBLIC_API_URL ||
  "http://localhost:8000";

export async function POST(req: NextRequest): Promise<NextResponse> {
  const body = await req.text();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const key = process.env.AIDIGEST_API_KEY;
  if (key) headers["X-API-Key"] = key;

  try {
    const res = await fetch(`${API_BASE}/api/feedback`, {
      method: "POST",
      headers,
      body,
      cache: "no-store",
    });
    const text = await res.text();
    return new NextResponse(text || "{}", {
      status: res.status,
      headers: { "Content-Type": "application/json" },
    });
  } catch (err) {
    const detail = err instanceof Error ? err.message : "upstream unreachable";
    return NextResponse.json({ detail }, { status: 502 });
  }
}
