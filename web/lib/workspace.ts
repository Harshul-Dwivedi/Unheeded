"use client";
import { useEffect, useState } from "react";

// A workspace = one team's memory graph (ws_<id>). Chosen in the header, remembered per browser.
export const WORKSPACES: Record<string, string> = { desk: "Journalist desk", regulator: "Safety regulator" };
const KEY = "unheeded-ws";
const EVENT = "unheeded-ws";

export function getWorkspace(): string {
  try {
    const w = window.localStorage.getItem(KEY);
    if (w && /^[a-z0-9_]{1,40}$/.test(w)) return w;
  } catch {}
  return "desk";
}

export function setWorkspace(w: string) {
  try { window.localStorage.setItem(KEY, w); } catch {}
  window.dispatchEvent(new Event(EVENT));
}

export function useWorkspace(): string | null {
  const [ws, setWs] = useState<string | null>(null); // null until mounted: no SSR guess
  useEffect(() => {
    setWs(getWorkspace());
    const on = () => setWs(getWorkspace());
    window.addEventListener(EVENT, on);
    return () => window.removeEventListener(EVENT, on);
  }, []);
  return ws;
}

/** Bumped after an action that changes counts in the header (review, approval). */
export function notifyCounts() {
  window.dispatchEvent(new Event("unheeded-counts"));
}
