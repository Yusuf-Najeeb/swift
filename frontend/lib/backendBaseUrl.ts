export function serverBackendBaseUrl(): string {
  const raw =
    process.env.SWIFT_BACKEND_URL?.trim() ||
    process.env.NEXT_PUBLIC_API_URL?.trim() ||
    "http://127.0.0.1:8000";
  return raw.replace(/\/$/, "");
}
