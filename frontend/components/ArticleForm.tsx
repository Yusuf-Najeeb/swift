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
      className="flex flex-col gap-4 font-sans"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
    >
      <div>
        <label
          htmlFor={`${id}-topic`}
          className="mb-1 block text-sm font-medium text-foreground"
        >
          Topic
        </label>
        <input
          id={`${id}-topic`}
          required
          className="w-full rounded-md border border-emerald-500/40 px-3 py-2 text-sm text-foreground outline-none ring-emerald-500/40 focus:ring-2"
          placeholder="What should the article cover?"
          value={value.topic}
          disabled={disabled}
          onChange={(e) => onChange({ ...value, topic: e.target.value })}
        />
      </div>
      <div>
        <label
          htmlFor={`${id}-aud`}
          className="mb-1 block text-sm font-medium text-foreground"
        >
          Audience
        </label>
        <input
          id={`${id}-aud`}
          className="w-full rounded-md border border-emerald-500/40 px-3 py-2 text-sm text-foreground outline-none focus:ring-2 focus:ring-emerald-500/40"
          placeholder="Who is this for? (optional)"
          value={value.audience}
          disabled={disabled}
          onChange={(e) => onChange({ ...value, audience: e.target.value })}
        />
      </div>
      <div>
        <label
          htmlFor={`${id}-tone`}
          className="mb-1 block text-sm font-medium text-foreground"
        >
          Tone
        </label>
        <input
          id={`${id}-tone`}
          className="w-full rounded-md border border-emerald-500/40 px-3 py-2 text-sm text-foreground outline-none focus:ring-2 focus:ring-emerald-500/40"
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
          className="border-t border-foreground pt-3 text-xs text-foreground"
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
