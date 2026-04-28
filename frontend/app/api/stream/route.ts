import { serverBackendBaseUrl } from "@/lib/backendBaseUrl";
import { backendAuthHeaders } from "@/lib/serverBackendAuth";
import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  const body = await request.text();

  const upstream = await fetch(
    `${serverBackendBaseUrl()}/api/generate/stream`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...backendAuthHeaders(),
      },
      body,
    }
  );

  if (!upstream.ok) {
    const errText = await upstream.text();
    return new Response(errText || upstream.statusText, {
      status: upstream.status,
      statusText: upstream.statusText,
    });
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type":
        upstream.headers.get("content-type") ?? "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}
