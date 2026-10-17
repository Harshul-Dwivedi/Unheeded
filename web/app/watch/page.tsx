"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import Status from "@/components/Status";
import { api } from "@/lib/api";
import { loadApi, peekApi } from "@/lib/cache";
import { date, place } from "@/lib/format";
import { useWorkspace, WORKSPACES } from "@/lib/workspace";

type Watch = {
  kind: "Recommendation" | "Accident" | "Organization"; target_id: string; created: number;
  alerts: { srid: string; change: string; at: number }[];
  now: { code?: string; label?: string; city?: string; state?: string; event_date?: number; open?: number } | null;
};

type Settings = { status: boolean; repeat: boolean; letters: boolean; new: boolean };
type Refresh = { enabled: boolean; running: boolean; error?: string | null; started?: number | null;
  last?: { changes: { srid: string; change: string[] }[]; alerts: Record<string, unknown[]>; seconds: number; finished: number } | null };
const KINDS: [keyof Settings, string][] = [["status", "Status changes and closures"], ["repeat", "Warnings repeated or reclassified in a new report"],
  ["letters", "New letters from either side"], ["new", "New recommendations"]];

const href = (w: Watch) => (w.kind === "Recommendation" ? `/rec/${w.target_id}` : w.kind === "Accident" ? `/accident/${w.target_id}` : `/org/${encodeURIComponent(w.target_id)}`);
const when = (ms: number) => new Date(ms).toISOString().slice(0, 10);

export default function WatchPage() {
  const ws = useWorkspace();
  const [watches, setWatches] = useState<Watch[] | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [refresh, setRefresh] = useState<Refresh | null>(() => peekApi<Refresh>("/api/refresh"));
  const [msg, setMsg] = useState("");
  const load = useCallback(() => {
    if (!ws) return;
    const w = peekApi<{ watches: Watch[] }>(`/api/workspaces/${ws}/watches`);
    if (w) setWatches(w.watches);
    const s = peekApi<Settings>(`/api/workspaces/${ws}/alert-settings`);
    if (s) setSettings(s);
    loadApi<{ watches: Watch[] }>(`/api/workspaces/${ws}/watches`).then((r) => setWatches(r.watches)).catch(() => setWatches([]));
    loadApi<Settings>(`/api/workspaces/${ws}/alert-settings`).then(setSettings).catch(() => {});
  }, [ws]);
  useEffect(load, [load]);
  useEffect(() => { loadApi<Refresh>("/api/refresh").then(setRefresh).catch(() => {}); }, []);
  useEffect(() => {  // while a refresh runs, poll until it finishes, then reload alerts
    if (!refresh?.running) return;
    const t = setInterval(() => api<Refresh>("/api/refresh").then((r) => { setRefresh(r); if (!r.running) load(); }).catch(() => {}), 2000);
    return () => clearInterval(t);
  }, [refresh?.running, load]);
  const toggle = async (k: keyof Settings) => {
    if (!ws || !settings) return;
    setSettings(await api<Settings>(`/api/workspaces/${ws}/alert-settings`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ [k]: !settings[k] }) }));
  };
  const startRefresh = async () => {
    setMsg("");
    try { await api("/api/refresh", { method: "POST" }); setRefresh((r) => ({ ...(r as Refresh), running: true })); }
    catch (e) { setMsg((e as Error).message); }
  };
  const alerts = (watches ?? []).flatMap((w) => w.alerts.map((a) => ({ ...a, w }))).sort((a, b) => b.at - a.at);
  return (
    <main className="wrap main">
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "flex-end", gap: 16 }}>
      <div style={{ maxWidth: 760 }}>
        <div className="k">Watch · {ws ? WORKSPACES[ws] ?? ws : ""}{refresh?.last ? ` · last refresh ${when(refresh.last.finished)}` : ""}</div>
        <h1 className="h1">What changed in the record</h1>
        <p style={{ margin: "10px 0 0", fontSize: 16, color: "var(--sub)" }}>
          When the record changes (a new status, a new letter, a warning repeated in a new report), a breadth-first walk through the graph from the
          changed warning finds every crash and organisation it reaches, and raises an alert on anything you follow.
        </p>
      </div>
      {refresh?.enabled && (
        <button type="button" className="b1" onClick={startRefresh} disabled={refresh.running}>{refresh.running ? "Refreshing… (reload + re-enrich)" : "Check for a newer export"}</button>
      )}
      </div>
      {msg && <p className="sig" role="alert">{msg}</p>}
      {refresh?.error && <p className="sig" role="alert">Last refresh failed: {refresh.error}</p>}
      {refresh?.last && !refresh.running && (
        <p style={{ margin: 0, fontSize: 14, color: "var(--sub)" }}>
          Last refresh: {refresh.last.changes.length} recommendation{refresh.last.changes.length === 1 ? "" : "s"} changed, alerts in {Object.keys(refresh.last.alerts).length} workspace{Object.keys(refresh.last.alerts).length === 1 ? "" : "s"}, {refresh.last.seconds} s.
        </p>
      )}
      <div className="cols">
        <section className="sec" style={{ flex: "999 1 640px", minWidth: 0 }}>
          <div className="k" style={{ marginBottom: 6 }}>Alerts</div>
          {alerts.length === 0 && (
            <p style={{ color: "var(--sub)", fontSize: 14 }}>
              No alerts yet. Alerts appear when a newer CAROL export is loaded (<span className="mono">make refresh DATA=…</span>) and it changes
              something you follow: a status, a closure, a new letter, or a new repeat in a report.
            </p>
          )}
          {alerts.map((a, i) => (
            <div key={i} style={{ display: "grid", gridTemplateColumns: "120px 120px minmax(0, 1fr)", gap: 14, padding: "16px 0", borderTop: "1px solid var(--rule2)", alignItems: "baseline" }}>
              <span className="mono" style={{ fontSize: 13, color: "var(--sub)" }}>{when(a.at)}</span>
              <span className="mono" style={{ fontSize: 11, letterSpacing: "0.08em", color: "var(--sub)" }}>CHANGE</span>
              <span><Link href={`/rec/${a.srid}`} className="mono">{a.srid}</Link> · {a.change} · reached <Link href={href(a.w)}>{a.w.target_id}</Link></span>
            </div>
          ))}
        </section>
        <aside className="ca">
          <section className="sec">
            <div className="k" style={{ marginBottom: 6 }}>Tell me about</div>
            <div style={{ display: "flex", flexDirection: "column", fontSize: 14 }}>
              {KINDS.map(([k, label]) => (
                <label key={k} style={{ display: "flex", alignItems: "center", gap: 12, minHeight: 44, borderBottom: "1px solid var(--rule2)" }}>
                  <input type="checkbox" checked={!!settings?.[k]} onChange={() => toggle(k)} disabled={!settings} style={{ width: 18, height: 18, accentColor: "var(--ink)" }} /> {label}
                </label>
              ))}
            </div>
            {refresh && !refresh.enabled && (
              <p style={{ margin: "10px 0 0", fontSize: 12, color: "var(--sub)" }}>
                Refresh rewrites the official graph, so it is off by default. To enable it, start the API with <span className="mono">ALLOW_REFRESH=1</span> and <span className="mono">REFRESH_DATA_DIR</span>, or run <span className="mono">make refresh DATA=…</span>.
              </p>
            )}
          </section>
          <section className="sec">
            <div className="k" style={{ marginBottom: 6 }}>You follow</div>
            {watches && watches.length === 0 && <p style={{ fontSize: 14 }}>Nothing yet. Use “Watch” on a warning, crash or organisation page.</p>}
            <div style={{ display: "flex", flexDirection: "column", fontSize: 14 }}>
              {watches?.map((w, i) => (
                <div key={w.kind + w.target_id} style={{ display: "flex", justifyContent: "space-between", gap: 10, padding: "11px 0", borderTop: "1px solid var(--rule2)", ...(i === watches.length - 1 ? { borderBottom: "1px solid var(--rule2)" } : {}) }}>
                  <span><Link href={href(w)} className={w.kind === "Organization" ? undefined : "mono"}>{w.target_id}</Link> · {w.kind === "Recommendation" ? "warning" : w.kind === "Accident" ? "accident" : "organisation"}</span>
                  {w.kind === "Recommendation" && w.now ? <Status code={w.now.code} label={w.now.label} />
                    : w.kind === "Accident" && w.now ? <span style={{ color: "var(--sub)" }}>{place(w.now.city, w.now.state)} · {date(w.now.event_date)}</span>
                    : <span style={{ color: "var(--sub)" }}>{w.now?.open ?? 0} open</span>}
                </div>
              ))}
            </div>
          </section>
        </aside>
      </div>
    </main>
  );
}
