"use client";

import {
  ArticleForm,
  defaultArticleFormState,
  type FormState,
} from "@/components/ArticleForm";
import { ArticlePreview } from "@/components/ArticlePreview";
import { SavedArticlesList } from "@/components/SavedArticlesList";
import { describePipelineEvent } from "@/lib/pipelineStatus";
import { streamSseJson } from "@/lib/parseSse";
import type { FinalArticle, PipelineEventData } from "@/lib/pipelineTypes";
import { isRunCompleted, isRunFailed } from "@/lib/pipelineTypes";
import { useCallback, useRef, useState } from "react";

function buildRequestJson(form: FormState) {
  return JSON.stringify({
    brief: {
      topic: form.topic.trim(),
      tone: (form.tone || "professional").trim(),
      audience: form.audience.trim() || null,
    },
  });
}

function toPipelineEvent(data: unknown): PipelineEventData {
  if (data && typeof data === "object" && "type" in data) {
    return data as PipelineEventData;
  }
  throw new Error("Invalid SSE event payload");
}

type RunStatus = "idle" | "running" | "done" | "error";

export default function Home() {
  const [form, setForm] = useState<FormState>(defaultArticleFormState);
  const [article, setArticle] = useState<FinalArticle | null>(null);
  const [status, setStatus] = useState<RunStatus>("idle");
  const [statusLine, setStatusLine] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [savedListToken, setSavedListToken] = useState(0);
  const abortRef = useRef<AbortController | null>(null);

  const run = useCallback(async () => {
    setArticle(null);
    setErrorMessage(null);
    setStatusLine("Connecting…");
    setStatus("running");
    const ac = new AbortController();
    abortRef.current = ac;

    try {
      const res = await fetch("/api/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: buildRequestJson(form),
        signal: ac.signal,
      });
      if (!res.ok) {
        const t = await res.text();
        throw new Error(t || res.statusText);
      }
      for await (const { data } of streamSseJson(res)) {
        const ev = toPipelineEvent(data);
        setStatusLine(describePipelineEvent(ev));
        if (isRunCompleted(ev)) {
          setArticle(ev.article);
          setStatus("done");
          setSavedListToken((n) => n + 1);
        }
        if (isRunFailed(ev)) {
          setErrorMessage(`${ev.error_type}: ${ev.error}`);
          setStatusLine(null);
          setStatus("error");
        }
      }
    } catch (e) {
      if (e instanceof Error && e.name === "AbortError") {
        setStatusLine(null);
        setStatus("idle");
        return;
      }
      setErrorMessage(e instanceof Error ? e.message : String(e));
      setStatusLine(null);
      setStatus("error");
    } finally {
      abortRef.current = null;
    }
  }, [form]);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const busy = status === "running";

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <header className="border-b border-zinc-800 bg-zinc-900/80 px-4 py-4 sm:px-8 text-center">
        <h1 className="text-[3rem] font-semibold tracking-tight text-zinc-50">
          Swift
        </h1>
        <p className="mt-1 text-xl text-zinc-500">
        Get your technical writing done faster.
        </p>
      </header>
      <main className="mx-auto flex flex-col lg:flex-row gap-8 p-4 pb-12 sm:p-8">
        <section className="w-full lg:max-w-[400px]">
          <ArticleForm
            value={form}
            onChange={setForm}
            disabled={busy}
            onSubmit={run}
            statusLine={statusLine}
          />
          {busy && (
            <button
              type="button"
              className="mt-3 w-full rounded-md border border-zinc-600 py-2 text-sm text-zinc-300 hover:bg-zinc-800"
              onClick={cancel}
            >
              Cancel
            </button>
          )}
          <SavedArticlesList
            refreshToken={savedListToken}
            onLoadArticle={setArticle}
          />
        </section>
        <section>
          {errorMessage && (
            <div
              className="mb-3 rounded-md border border-red-500/40 bg-red-950/40 px-3 py-2 text-sm text-red-200"
              role="alert"
            >
              {errorMessage}
            </div>
          )}
          <ArticlePreview article={article} />
        </section>
      </main>
    </div>
  );
}
