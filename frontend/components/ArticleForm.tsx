"use client";

import { useId } from "react";

export type FormState = {
  topic: string;
  audience: string;
  tone: string;
};

export const defaultArticleFormState: FormState = {
  topic: "",
  audience: "",
  tone: "professional",
};

type Props = {
  value: FormState;
  onChange: (s: FormState) => void;
  disabled: boolean;
  onSubmit: () => void;
  /** Latest pipeline message (one line, under the form). */
  statusLine: string | null;
};

export function ArticleForm({
  value,
  onChange,
  disabled,
  onSubmit,
  statusLine,
}: Props) {
  const id = useId();
  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
    >
      <div>
        <label
          htmlFor={`${id}-topic`}
          className="mb-1 block text-sm font-medium text-zinc-300"
        >
          Topic
        </label>
        <input
          id={`${id}-topic`}
          required
          className="w-full rounded-md border border-zinc-600 bg-zinc-900/80 px-3 py-2 text-sm text-zinc-100 outline-none ring-emerald-500/40 focus:ring-2"
          placeholder="What should the article cover?"
          value={value.topic}
          disabled={disabled}
          onChange={(e) => onChange({ ...value, topic: e.target.value })}
        />
      </div>
      <div>
        <label
          htmlFor={`${id}-aud`}
          className="mb-1 block text-sm font-medium text-zinc-300"
        >
          Audience
        </label>
        <input
          id={`${id}-aud`}
          className="w-full rounded-md border border-zinc-600 bg-zinc-900/80 px-3 py-2 text-sm text-zinc-100 outline-none focus:ring-2 focus:ring-emerald-500/40"
          placeholder="Who is this for? (optional)"
          value={value.audience}
          disabled={disabled}
          onChange={(e) => onChange({ ...value, audience: e.target.value })}
        />
      </div>
      <div>
        <label
          htmlFor={`${id}-tone`}
          className="mb-1 block text-sm font-medium text-zinc-300"
        >
          Tone
        </label>
        <input
          id={`${id}-tone`}
          className="w-full rounded-md border border-zinc-600 bg-zinc-900/80 px-3 py-2 text-sm text-zinc-100 outline-none focus:ring-2 focus:ring-emerald-500/40"
          placeholder="e.g. professional, casual, academic"
          value={value.tone}
          disabled={disabled}
          onChange={(e) => onChange({ ...value, tone: e.target.value })}
        />
      </div>
      <button
        type="submit"
        disabled={disabled || !value.topic.trim()}
        className="rounded-md bg-emerald-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {disabled ? "Generating…" : "Generate article"}
      </button>
      {statusLine !== null && statusLine !== "" && (
        <p
          className="border-t border-zinc-800 pt-3 text-xs text-zinc-500"
          aria-live="polite"
        >
          {disabled && (
            <span className="mr-2 inline-block size-1.5 animate-pulse rounded-full bg-emerald-500" />
          )}
          {statusLine}
        </p>
      )}
    </form>
  );
}
