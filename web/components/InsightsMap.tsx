"use client";
import Link from "next/link";
import { useState } from "react";
import type { Insights } from "@/lib/api";
import { date, place } from "@/lib/format";
import UsMap from "./UsMap";

/** Map (or table) of the crashes with a linked warning open, filterable to one warning (its origin marked). */
export default function InsightsMap({ data }: { data: Insights }) {
  const [only, setOnly] = useState<string | null>(null);
  const [table, setTable] = useState(false);
  const crashes = data.crashes.filter((c) => !only || c.open_warnings.includes(only));
  const origin = only ? data.origins.find((o) => o.srid === only) : null;
  const killed = crashes.reduce((n, c) => n + (c.fatal ?? 0), 0);
  return (
    <section className="sec">
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "flex-end", gap: 12, marginBottom: 20 }}>
        <div>
          <h2 className="serif" style={{ margin: 0, fontSize: 28, fontWeight: 500 }}>Where crashes happened while a linked warning was open</h2>
          <p style={{ margin: "4px 0 0", fontSize: 13, color: "var(--sub)" }}>
            {only ? `${crashes.length} crashes linked to ${only}, ${killed} killed${origin ? ` · first issued after ${place(origin.city, origin.state)}` : ""}`
                  : `${crashes.length} crashes, ${killed} killed · pin size grows with deaths`}
          </p>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }} role="group" aria-label="Filter">
          <button type="button" onClick={() => setOnly(null)} className={`seg${!only ? " on" : ""}`} aria-pressed={!only}>All {data.crashes.length}</button>
          {data.filters.map((f) => (
            <button key={f.srid} type="button" onClick={() => setOnly(f.srid)} className={`seg${only === f.srid ? " on" : ""}`} aria-pressed={only === f.srid}>Only {f.srid}</button>
          ))}
          <button type="button" onClick={() => setTable(!table)} className="seg" style={{ marginLeft: 10 }}>{table ? "View as map" : "View as table"}</button>
        </div>
      </div>
      {!table ? (
        <div style={{ maxWidth: 980 }}>
          <UsMap alt={`Map of the United States: ${crashes.length} crashes with a linked warning open`}
            points={[...crashes.map((c) => ({ lat: c.lat, lon: c.lon, fatal: c.fatal, label: `${place(c.city, c.state)} · ${date(c.event_date)} · ${c.fatal} killed`, href: `/accident/${c.ntsb_num}` }))]} />
          <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 22, marginTop: 14, fontSize: 12, color: "var(--sub)" }}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><span style={{ width: 10, height: 10, borderRadius: "50%", background: "var(--signal)" }} />No deaths</span>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><span style={{ width: 20, height: 20, borderRadius: "50%", background: "var(--signal)" }} />10 killed</span>
            <span>Alaska and Hawaii shown as insets</span>
            {origin && <span>Origin: {place(origin.city, origin.state)} (<Link href={`/accident/${origin.ntsb_num}`} className="mono">{origin.ntsb_num}</Link>)</span>}
          </div>
        </div>
      ) : (
        <div style={{ maxWidth: 980 }}>
          <div className="tr k" style={{ borderBottom: "1px solid var(--ink)" }}><span>Crash</span><span>Date</span><span>Killed</span><span>Linked warnings open</span></div>
          {crashes.map((c) => (
            <div key={c.ntsb_num} className="tr" style={{ borderBottom: "1px solid var(--rule2)" }}>
              <Link href={`/accident/${c.ntsb_num}`}>{place(c.city, c.state)}</Link><span className="mono" style={{ fontSize: 13, color: "var(--sub)" }}>{date(c.event_date)}</span>
              <span className="serif" style={{ fontSize: 18 }}>{c.fatal}</span><span>{c.open_warnings.length}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
