import { backendAuthHeaders } from "@/lib/serverBackendAuth";
import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function backendBase(): string {
  const raw = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
  return raw.replace(/\/$/, "");
}

export async function GET(
  _request: NextRequest,
  context: { params: Promise<{ filename: string }> }
) {
  const { filename } = await context.params;
  if (!filename) {
    return new Response("Missing filename", { status: 400 });
  }
  const safe = encodeURIComponent(filename);
  const upstream = await fetch(`${backendBase()}/api/articles/${safe}`, {
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
