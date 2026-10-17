"use client";
import type { AccidentPage, RecPage, RunResult, Step } from "@/lib/api";

/** Last Investigate run in this tab. Survives Next.js unmounting the page when you switch nav tabs. */
export type InvestigateSnap = {
  ws: string;
  q: string;
  asked: string;
  phase: "idle" | "running" | "done" | "error";
  steps: (Step & { summary?: string })[];
  result: RunResult | null;
  acc: AccidentPage | null;
  chain: RecPage | null;
  error: string;
  thread: string | null;
};

const INV = "unheeded-investigate";
const REV = "unheeded-review";

let investigate: InvestigateSnap | null = null;
const reviewByWs = new Map<string, unknown>();

function read<T>(key: string): T | null {
  try {
    const raw = sessionStorage.getItem(key);
    return raw ? JSON.parse(raw) as T : null;
  } catch {
    return null;
  }
}

function write(key: string, value: unknown) {
  try { sessionStorage.setItem(key, JSON.stringify(value)); } catch {}
}

export function getInvestigate(ws: string): InvestigateSnap | null {
  if (investigate?.ws === ws) return investigate;
  const stored = read<InvestigateSnap>(INV);
  if (stored?.ws === ws) {
    investigate = stored;
    return stored;
  }
  return null;
}

export function setInvestigate(s: InvestigateSnap) {
  investigate = s;
  write(INV, s);
}

export function patchInvestigateBrief(invId: string, brief: RunResult["brief"]) {
  if (!investigate?.result || investigate.result.investigation !== invId) return;
  investigate = { ...investigate, result: { ...investigate.result, brief } };
  write(INV, investigate);
}

export function getReview<T>(ws: string): T | null {
  if (reviewByWs.has(ws)) return reviewByWs.get(ws) as T;
  const stored = read<{ ws: string; data: T }>(REV);
  if (stored?.ws === ws) {
    reviewByWs.set(ws, stored.data);
    return stored.data;
  }
  return null;
}

export function setReview<T>(ws: string, data: T) {
  reviewByWs.set(ws, data);
  write(REV, { ws, data });
}
