"use client";
import { useState } from "react";

export type ThreadEvent = { date: number; when: string; who: string; kind: string; text: string; hot?: boolean };

/** The letter thread with the prototype's filters: All · NTSB · <owner> · Repeated after crashes (mentions show under All only). */
export default function Thread({ events, owner }: { events: ThreadEvent[]; owner: string }) {
  const [f, setF] = useState("all");
  const filters: [string, string][] = [["all", "All"], ["ntsb", "NTSB"], ["owner", owner], ["crash", "Repeated after crashes"]];
  const shown = events.filter((e) => f === "all" || e.kind === f);
  const tag: React.CSSProperties = { fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, letterSpacing: "0.08em", color: "var(--sub)" };
  return (
    <>
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: 10, marginBottom: 10 }}>
        <div className="k">The letter thread · {events.length} entries</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }} role="group" aria-label="Filter the thread">
          {filters.map(([k, label]) => (
            <button key={k} type="button" onClick={() => setF(k)} className={`seg${f === k ? " on" : ""}`} aria-pressed={f === k}>{label}</button>
          ))}
        </div>
      </div>
      <div style={{ display: "flex", flexDirection: "column" }}>
        {shown.map((e, i) => (
          <div key={i} className="row" style={{ display: "grid", gridTemplateColumns: "110px 100px minmax(0, 1fr)", gap: 16, padding: "12px 0", fontSize: 14, alignItems: "baseline" }}>
            <span className="mono" style={{ fontSize: 13, color: "var(--sub)" }}>{e.when}</span>
            <span style={e.hot ? { ...tag, color: "var(--signal)", fontWeight: 500 } : tag}>{e.who}</span>
            <span style={e.hot ? { fontFamily: "'Newsreader', Georgia, serif", fontSize: 18, color: "var(--signal)" } : { color: "var(--ink)" }}>{e.text}</span>
          </div>
        ))}
        {!shown.length && <div className="row" style={{ padding: "12px 0", color: "var(--sub)" }}>Nothing of this kind.</div>}
      </div>
    </>
  );
}
