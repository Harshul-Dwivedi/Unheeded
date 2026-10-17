"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";

/** Watch toggle (Accident / Recommendation / Organisation). Watches live in the workspace graph. */
export default function WatchButton({ kind, id, label, primary }: { kind: "Accident" | "Recommendation" | "Organization"; id: string; label: string; primary?: boolean }) {
  const ws = useWorkspace();
  const [on, setOn] = useState(false);
  useEffect(() => {
    if (!ws) return;
    api<{ watches: { kind: string; target_id: string }[] }>(`/api/workspaces/${ws}/watches`)
      .then((r) => setOn(r.watches.some((w) => w.kind === kind && w.target_id === id))).catch(() => {});
  }, [ws, kind, id]);
  const add = async () => {
    if (!ws || on) return;
    await api(`/api/workspaces/${ws}/watches`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind, target_id: id }) });
    setOn(true);
  };
  return (
    <button type="button" onClick={add} className={primary ? "b1" : "b2"} style={{ justifyContent: "center" }} aria-pressed={on}>
      {on ? "Watching ✓" : label}
    </button>
  );
}
