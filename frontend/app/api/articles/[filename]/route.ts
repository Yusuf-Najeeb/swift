import { serverBackendBaseUrl } from "@/lib/backendBaseUrl";
import { backendAuthHeaders } from "@/lib/serverBackendAuth";
import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(
  _request: NextRequest,
  context: { params: Promise<{ filename: string }> }
) {
  const { filename } = await context.params;
  if (!filename) {
    return new Response("Missing filename", { status: 400 });
  }
  const safe = encodeURIComponent(filename);
  const upstream = await fetch(`${serverBackendBaseUrl()}/api/articles/${safe}`, {
    method: "GET",
    headers: { ...backendAuthHeaders() },
    cache: "no-store",
  });

  if (!upstream.ok) {
    const errText = await upstream.text();
    return new Response(errText || upstream.statusText, {
      status: upstream.status,
    });
  }

  const body = await upstream.text();
  return new Response(body, {
    status: 200,
    headers: {
      "Content-Type": "text/markdown; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
    },
  });
}
