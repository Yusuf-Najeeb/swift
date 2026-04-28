"use client";

import { parseSavedMarkdown } from "@/lib/parseSavedMarkdown";
import type { FinalArticle } from "@/lib/pipelineTypes";
import { useCallback, useEffect, useState } from "react";

export type SavedListItem = {
  filename: string;
  title: string;
  url_path: string;
  size_bytes: number;
  modified_utc: string;
};

type Props = {
  refreshToken: number;
  onLoadArticle: (article: FinalArticle) => void;
};

function formatBytes(n: number): string {
  if (n < 1024) {
    return `${n} B`;
  }
  return `${(n / 1024).toFixed(1)} KB`;
}

function formatWhen(iso: string): string {
  const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) {
    return iso;
  }
  return d.toLocaleString();
}

export function SavedArticlesList({ refreshToken, onLoadArticle }: Props) {
  const [items, setItems] = useState<SavedListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loadingFile, setLoadingFile] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    setLoading(true);
    try {
      const res = await fetch("/api/articles", { cache: "no-store" });
      if (!res.ok) {
        throw new Error((await res.text()) || res.statusText);
      }
      const data = (await res.json()) as { articles?: SavedListItem[] };
      setItems(data.articles ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshToken]);

  const loadIntoPreview = async (filename: string) => {
    setLoadingFile(filename);
    setError(null);
    try {
      const res = await fetch(
        `/api/articles/${encodeURIComponent(filename)}`,
        { cache: "no-store" }
      );
      if (!res.ok) {
        throw new Error((await res.text()) || res.statusText);
      }
      const text = await res.text();
      const { title, summary, body_markdown } = parseSavedMarkdown(text);
      onLoadArticle({
        title,
        summary,
        body_markdown,
        images: [],
        diagrams: [],
        image_placeholder_count: 0,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingFile(null);
    }
  };

  return (
    <div className="mt-6 rounded-lg border border-zinc-700/80 bg-background p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-foreground">
          Saved articles
        </h2>
        <button
          type="button"
          disabled={loading}
          onClick={() => void load()}
          className="rounded border border-emerald-500/60 px-2 py-1 text-xs text-foreground hover:bg-emerald-500/40 disabled:opacity-50"
        >
          {loading ? "Loading…" : "Refresh"}
        </button>
      </div>
      {error && (
        <p className="mb-2 text-xs text-red-500" role="alert">
          {error}
        </p>
      )}
      {loading && items.length === 0 && !error ? (
        <p className="text-sm text-zinc-500">Loading list…</p>
      ) : null}
      {!loading && items.length === 0 && !error ? (
        <p className="text-sm text-zinc-500">
          No saved articles yet. They appear here after a successful run.
        </p>
      ) : null}
      {items.length > 0 ? (
        <ul className="max-h-64 space-y-2 overflow-y-auto text-sm">
          {items.map((a) => (
            <li
              key={a.filename}
              className="flex flex-col gap-1 border-b border-zinc-800 pb-2 last:border-0 last:pb-0"
            >
              <div className="font-medium text-zinc-100 line-clamp-2">
                {a.title}
              </div>
              <div className="text-xs text-zinc-500">
                {formatWhen(a.modified_utc)} · {formatBytes(a.size_bytes)}
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  className="rounded border border-zinc-600 bg-zinc-800 px-2 py-0.5 text-xs text-zinc-200 hover:bg-zinc-700"
                  disabled={loadingFile === a.filename}
                  onClick={() => void loadIntoPreview(a.filename)}
                >
                  {loadingFile === a.filename ? "Loading…" : "View in preview"}
                </button>
                <a
                  href={`/api/articles/${encodeURIComponent(a.filename)}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="rounded border border-zinc-600 bg-zinc-800/50 px-2 py-0.5 text-xs text-zinc-300 hover:bg-zinc-800"
                >
                  Open file
                </a>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
