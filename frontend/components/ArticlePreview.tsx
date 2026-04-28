"use client";

import type { FinalArticle } from "@/lib/pipelineTypes";
import React, { isValidElement } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { MermaidBlock } from "./MermaidBlock";

type Props = {
  article: FinalArticle | null;
};

export function ArticlePreview({ article }: Props) {
  if (!article) {
    return (
      <div className="rounded-lg border border-dashed border-emerald-500/60 bg-background p-6 text-center text-sm text-foreground font-sans">
        When the pipeline finishes, the final article (Markdown) appears
        here. Mermaid diagrams render when possible.
      </div>
    );
  }

  const md = article.body_markdown;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-start justify-between gap-2 font-sans">
        <div>
          <h2 className="text-lg font-semibold text-foreground font-heading">
            {article.title}
          </h2>
          <p className="text-sm text-foreground">{article.summary}</p>
        </div>
        <button
          type="button"
          className="shrink-0 rounded-md border border-emerald-500/60 px-3 py-1.5 text-xs font-medium text-foreground hover:bg-emerald-500/40"
          onClick={() => downloadMarkdown(article)}
        >
          Download .md
        </button>
      </div>
      <article className="prose-article min-w-0 rounded-lg border border-emerald-500/40 bg-background p-4">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            pre: PreWithMermaid,
            h1: (p) => (
              <h1
                className="mb-3 mt-0 text-2xl font-bold text-foreground"
                {...p}
              />
            ),
            h2: (p) => (
              <h2
                className="mb-2 mt-6 text-xl font-semibold text-foreground"
                {...p}
              />
            ),
            h3: (p) => (
              <h3 className="mb-2 mt-4 text-lg font-medium text-foreground" {...p} />
            ),
            p: (p) => <p className="mb-3 leading-relaxed" {...p} />,
            ul: (p) => (
              <ul className="mb-3 list-inside list-disc space-y-1 text-foreground" {...p} />
            ),
            ol: (p) => (
              <ol
                className="mb-3 list-inside list-decimal space-y-1 text-foreground"
                {...p}
              />
            ),
            a: (p) => (
              <a
                className="text-emerald-500 underline-offset-2 hover:underline"
                {...p}
              />
            ),
            blockquote: (p) => (
              <blockquote
                className="my-3 border-l-4 border-emerald-500/40 pl-4 text-foreground italic"
                {...p}
              />
            ),
            code: (p) => {
              const inline = !("className" in p) || !p.className;
              if (inline) {
                return (
                  <code
                    className="rounded bg-background px-1 py-0.5 font-mono text-sm text-foreground"
                    {...p}
                  />
                );
              }
              return <code {...p} />;
            },
            img: (p) => (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                className="my-4 max-h-[480px] max-w-full rounded-lg border border-emerald-500/40 object-contain"
                alt={p.alt ?? ""}
                {...p}
              />
            ),
          }}
        >
          {md}
        </ReactMarkdown>
      </article>
    </div>
  );
}

function PreWithMermaid({
  children,
  ...rest
}: React.ComponentProps<"pre">) {
  const child = React.Children.only(children) as
    | React.ReactElement<{
        className?: string;
        children?: React.ReactNode;
      }>
    | string
    | number
    | null
    | undefined;

  if (isValidElement(child) && child.type === "code") {
    const cls = String(child.props.className ?? "");
    if (cls.includes("language-mermaid")) {
      const text = String(child.props.children).replace(/\n$/, "");
      return <MermaidBlock code={text} />;
    }
  }
  return (
    <pre
      className="my-4 overflow-x-auto rounded-md border border-emerald-500/40 bg-background p-3 text-foreground text-sm"
      {...rest}
    >
      {children}
    </pre>
  );
}

function slugify(title: string): string {
  return title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "")
    .slice(0, 80) || "article";
}

function downloadMarkdown(article: FinalArticle) {
  const blob = new Blob(
    [
      `# ${article.title}\n\n${article.summary}\n\n---\n\n${article.body_markdown}\n`,
    ],
    { type: "text/markdown;charset=utf-8" }
  );
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${slugify(article.title)}.md`;
  a.click();
  URL.revokeObjectURL(url);
}
