/**
 * Parse FastAPI + sse-starlette frames.
 *
 * `sse-starlette` uses CRLF line endings, so the blank line *between* events
 * is ``\r\n\r\n`` — in JS, ``\n\n`` is **not** a substring of that, so
 * a naive "\n\n" split never flushes a frame. The last event
 * (``run.completed`` with a large JSON payload) would then be stuck in the
 * buffer and the article never appears in the UI.
 *
 * After normalizing to LF, a frame looks like: ``event: T\ndata: {...}\n\n`` .
 *
 * The SSE spec allows multiple ``data:`` lines (split on ``\n`` in the
 * payload); we join them with ``\n`` before ``JSON.parse``.
 */
export async function* streamSseJson(
  response: Response
): AsyncGenerator<{ event: string; data: unknown }> {
  const reader = response.body?.getReader();
  if (!reader) {
    throw new Error("Response has no body to read");
  }
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (value) {
      buffer += decoder.decode(value, { stream: true });
    }
    if (done) {
      buffer += decoder.decode();
    }

    buffer = normalizeCrlf(buffer);

    let separator: number;
    while ((separator = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, separator);
      buffer = buffer.slice(separator + 2);
      const msg = parseSseFrame(frame);
      if (msg) {
        yield { event: msg.event, data: JSON.parse(msg.data) as unknown };
      }
    }

    if (done) {
      const rest = buffer.trim();
      if (rest) {
        const msg = parseSseFrame(rest);
        if (msg) {
          try {
            yield { event: msg.event, data: JSON.parse(msg.data) as unknown };
          } catch {
            /* stream ended on incomplete chunk; ignore */
          }
        }
      }
      break;
    }
  }
}

function normalizeCrlf(s: string): string {
  return s.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
}

function parseSseFrame(frame: string): { event: string; data: string } | null {
  let eventName = "";
  const dataLines: string[] = [];

  for (const rawLine of frame.split("\n")) {
    if (!rawLine) {
      continue;
    }
    if (rawLine.startsWith(":")) {
      continue;
    }
    if (rawLine.startsWith("event:")) {
      eventName = rawLine.slice(6).trim();
      continue;
    }
    if (rawLine.startsWith("data:")) {
      dataLines.push(rawLine.slice(5).replace(/^\s/, ""));
    }
  }

  if (!eventName || dataLines.length === 0) {
    return null;
  }

  return { event: eventName, data: dataLines.join("\n") };
}
