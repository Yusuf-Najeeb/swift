"use client";

import mermaid from "mermaid";
import { useEffect, useId, useRef, useState } from "react";

let inited = false;
function initMermaid() {
  if (inited) {
    return;
  }
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "loose",
    theme: typeof window !== "undefined" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "neutral",
  });
  inited = true;
}

type Props = { code: string };

/**
 * Renders a single Mermaid code block as SVG in the client.
 * Falls back to a monospace pre on parse errors.
 */
export function MermaidBlock({ code }: Props) {
  const id = useId().replace(/:/g, "m");
  const host = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    initMermaid();
    if (!host.current) {
      return;
    }
    const rid = `mmd-${id}-${Math.random().toString(36).slice(2, 9)}`;
    let cancelled = false;
    mermaid
      .render(rid, code)
      .then(({ svg }) => {
        if (cancelled || !host.current) {
          return;
        }
        host.current.innerHTML = svg;
        setError(null);
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [code, id]);

  if (error) {
    return (
      <pre className="my-4 overflow-x-auto rounded-md border border-red-500/40 bg-zinc-950/40 p-3 text-xs text-red-300">
        {error}
        {"\n\n"}
        {code}
      </pre>
    );
  }
  return (
    <div
      ref={host}
      className="my-6 flex w-full min-w-0 justify-center overflow-x-auto text-sm [&_svg]:max-w-full"
    />
  );
}
