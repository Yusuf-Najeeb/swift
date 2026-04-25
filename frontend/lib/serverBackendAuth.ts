/**
 * Server-only (Route Handlers): forward optional bearer to FastAPI.
 * Set `SWIFT_API_BEARER_TOKEN` in `frontend/.env.local` to match the backend.
 */
export function backendAuthHeaders(): Record<string, string> {
  const t = process.env.SWIFT_API_BEARER_TOKEN?.trim();
  if (!t) {
    return {};
  }
  return { Authorization: `Bearer ${t}` };
}
