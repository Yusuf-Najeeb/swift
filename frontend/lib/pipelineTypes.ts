/**
 * TypeScript mirror of `backend/agents/events.py` + `ArticleBrief` / `FinalArticle`.
 * JSON from SSE `data:` lines matches these shapes (Pydantic `model_dump_json()`).
 */

export type ArticleLength = "short" | "medium" | "long";

export interface ArticleBrief {
  topic: string;
  tone?: string;
  length?: ArticleLength;
  keywords?: string[];
  audience?: string | null;
  extra_notes?: string | null;
}

export interface ImageAsset {
  description: string;
  url: string;
  alt_text: string;
}

export interface DiagramAsset {
  language: string;
  source: string;
}

export interface FinalArticle {
  title: string;
  summary: string;
  body_markdown: string;
  images: ImageAsset[];
  diagrams: DiagramAsset[];
  image_placeholder_count: number;
}

export interface SavedArticle {
  filename: string;
  relative_path: string;
  url_path: string;
}

export interface BaseEvent {
  type: string;
  timestamp: string;
}

export interface RunStartedEvent extends BaseEvent {
  type: "run.started";
  brief: ArticleBrief;
  max_retries: number;
}

export interface AttemptStartedEvent extends BaseEvent {
  type: "attempt.started";
  iteration: number;
}

export interface WriterCompletedEvent extends BaseEvent {
  type: "writer.completed";
  iteration: number;
  title: string;
  word_count: number;
  image_placeholder_count: number;
}

export interface EvaluatorCompletedEvent extends BaseEvent {
  type: "evaluator.completed";
  iteration: number;
  score: number;
  approved: boolean;
  strengths: string[];
  weaknesses: string[];
  suggestions: string[];
}

export interface ImagesStartedEvent extends BaseEvent {
  type: "images.started";
  placeholder_count: number;
}

export interface ImagesCompletedEvent extends BaseEvent {
  type: "images.completed";
  image_count: number;
  diagram_count: number;
}

export interface RunCompletedEvent extends BaseEvent {
  type: "run.completed";
  article: FinalArticle;
  saved?: SavedArticle | null;
  iterations: number;
  approved: boolean;
}

export interface RunFailedEvent extends BaseEvent {
  type: "run.failed";
  error: string;
  error_type: string;
}

export type PipelineEventData =
  | RunStartedEvent
  | AttemptStartedEvent
  | WriterCompletedEvent
  | EvaluatorCompletedEvent
  | ImagesStartedEvent
  | ImagesCompletedEvent
  | RunCompletedEvent
  | RunFailedEvent;

export function isRunCompleted(
  e: PipelineEventData
): e is RunCompletedEvent {
  return e.type === "run.completed";
}

export function isRunFailed(e: PipelineEventData): e is RunFailedEvent {
  return e.type === "run.failed";
}
