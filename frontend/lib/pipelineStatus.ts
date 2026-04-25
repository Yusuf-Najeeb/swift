import type { PipelineEventData } from "@/lib/pipelineTypes";

/** Single-line status for the compact UI under the form. */
export function describePipelineEvent(ev: PipelineEventData): string {
  switch (ev.type) {
    case "run.started":
      return "Starting pipeline…";
    case "attempt.started":
      return `Writing draft (attempt ${ev.iteration})…`;
    case "writer.completed":
      return "Draft ready — reviewing…";
    case "evaluator.completed": {
      const s = `Score ${ev.score}/10`;
      return ev.approved ? `${s} — approved` : `${s} — revising…`;
    }
    case "images.started":
      return "Resolving images…";
    case "images.completed":
      return "Finishing up…";
    case "run.completed":
      return ev.approved
        ? "Complete — article ready"
        : "Complete — see preview (below threshold)";
    case "run.failed":
      return `Failed: ${ev.error}`;
  }
}
