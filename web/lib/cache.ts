"use client";
import { api } from "@/lib/api";
import { setReview } from "@/lib/session";

/** In-tab GET cache so Insights/Review/Briefs/Watch can paint immediately after a prefetch. */
const mem = new Map<string, unknown>();

export function peekApi<T>(path: string): T | null {
  return (mem.get(path) as T | undefined) ?? null;
}

export function putApi(path: string, data: unknown) {
  mem.set(path, data);
}

export function dropApi(path: string) {
  mem.delete(path);
}

export async function loadApi<T>(path: string): Promise<T> {
  const data = await api<T>(path);
  mem.set(path, data);
  return data;
}

/** Warm the dashboard APIs (and Next routes, when a router is given) during a long investigation. */
export function prefetchDashboard(ws: string, refresh = false, router?: { prefetch: (href: string) => void }) {
  router?.prefetch("/");
  for (const href of ["/insights", "/review", "/agents", "/briefs", "/watch"]) router?.prefetch(href);
  const paths = [
    "/api/insights", "/api/eval", "/api/health", "/api/refresh",
    `/api/workspaces/${ws}/review`, `/api/workspaces/${ws}/investigations`,
    `/api/workspaces/${ws}/watches`, `/api/workspaces/${ws}/alert-settings`,
  ];
  for (const p of paths) {
    if (!refresh && mem.has(p)) continue;
    api(p).then((d) => {
      mem.set(p, d);
      if (p.endsWith("/review") && d && typeof d === "object" && "proposals" in (d as object)) setReview(ws, d);
    }).catch(() => {});
  }
}
