import { backendAuthHeaders } from "@/lib/serverBackendAuth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function backendBase(): string {
  const raw = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
  return raw.replace(/\/$/, "");
}

export async function GET() {
  const upstream = await fetch(`${backendBase()}/api/articles`, {
    method: "GET",
    headers: { Accept: "application/json", ...backendAuthHeaders() },
    cache: "no-store",
  });

  const text = await upstream.text();
  return new Response(text, {
    status: upstream.status,
    headers: { "Content-Type": "application/json" },
  });
}
