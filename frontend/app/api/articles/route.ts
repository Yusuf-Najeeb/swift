import { serverBackendBaseUrl } from "@/lib/backendBaseUrl";
import { backendAuthHeaders } from "@/lib/serverBackendAuth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  const upstream = await fetch(`${serverBackendBaseUrl()}/api/articles`, {
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
