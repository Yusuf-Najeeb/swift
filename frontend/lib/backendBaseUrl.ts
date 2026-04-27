/**
 * Upstream FastAPI base URL for server-side Route Handlers.
 *
 * Prefer `SWIFT_BACKEND_URL` (runtime, not baked at `next build`) so Azure
 * and Compose can set the public backend URL without rebuilding the image
 * when the hostname is stable (ingress FQDN) and avoids stale
 * `NEXT_PUBLIC_*` values.
 */
export function serverBackendBaseUrl(): string {
  const raw =
    process.env.SWIFT_BACKEND_URL?.trim() ||
    process.env.NEXT_PUBLIC_API_URL?.trim() ||
    "http://127.0.0.1:8000";
  return raw.replace(/\/$/, "");
}
