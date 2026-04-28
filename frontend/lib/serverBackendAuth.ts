export function backendAuthHeaders(): Record<string, string> {
  const t = process.env.SWIFT_API_BEARER_TOKEN?.trim();
  if (!t) {
    return {};
  }
  return { Authorization: `Bearer ${t}` };
}
