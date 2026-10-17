"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import InsightsMap from "@/components/InsightsMap";
import { type Insights } from "@/lib/api";
import { loadApi, peekApi } from "@/lib/cache";
import { asked } from "@/lib/format";

export default function InsightsPage() {
  const [d, setD] = useState<Insights | null>(() => peekApi<Insights>("/api/insights"));
  useEffect(() => { loadApi<Insights>("/api/insights").then(setD).catch(() => {}); }, []);
  if (!d) return <main className="wrap main"><div className="k">Insights · US aviation, 1960s to 2026</div><h1 className="h1">Sixty years of aviation warnings</h1></main>;
  return (
    <main className="wrap main">
      <div>
        <div className="k">Insights · US aviation, 1960s to 2026</div>
        <h1 className="h1">Sixty years of aviation warnings</h1>
      </div>
      <div className="facts sec">
        <div className="fact"><div className="fl">Aviation recommendations</div><div className="fv">{d.recommendations.toLocaleString("en-US")}</div></div>
        <div className="fact"><div className="fl">Crashes with a linked warning open</div><div className="fv">{d.linked_crashes}</div></div>
        <div className="fact"><div className="fl">Killed in those crashes</div><div className="fv" style={{ color: "var(--signal)" }}>{d.killed}</div></div>
        <div className="fact"><div className="fl">Open warnings linked to them</div><div className="fv">{d.links}</div></div>
      </div>
      <InsightsMap data={d} />
      <section className="sec" style={{ maxWidth: 980 }}>
        <div className="k" style={{ marginBottom: 4 }}>Rated unacceptable, and repeated most often by the NTSB</div>
        <div style={{ display: "flex", flexDirection: "column", fontSize: 14 }}>
          {d.most_repeated_unacceptable.map((r, i) => (
            <div key={r.srid} className="lb row" style={i === d.most_repeated_unacceptable.length - 1 ? { borderBottom: "1px solid var(--rule2)" } : undefined}>
              <span className="serif" style={{ fontSize: 20, color: "var(--muted)" }}>{i + 1}</span><Link href={`/rec/${r.srid}`} className="mono">{r.srid}</Link>
              <span>{asked(r.text, 90)}</span><span className="serif" style={{ fontSize: 20, textAlign: "right" }}>{r.times_reiterated}×</span>
            </div>
          ))}
        </div>
      </section>
      <section className="sec" style={{ maxWidth: 980 }}>
        <div className="k" style={{ marginBottom: 4 }}>Linked to the most crashes · PageRank over crash ↔ warning links (on 54 links it tracks the count)</div>
        <p style={{ margin: "4px 0 8px", fontSize: 13, color: "var(--sub)" }}>FalkorDB algo.pageRank, computed at load. On 54 links it tracks how many crashes a warning is linked to.</p>
        <div style={{ display: "flex", flexDirection: "column", fontSize: 14 }}>
          {d.leaders.slice(0, 8).map((r, i) => (
            <div key={r.srid} className="lb row">
              <span className="serif" style={{ fontSize: 20, color: "var(--muted)" }}>{i + 1}</span><Link href={`/rec/${r.srid}`} className="mono">{r.srid}</Link>
              <span>{asked(r.text, 90)}</span><span className="mono" style={{ textAlign: "right", color: "var(--sub)" }}>{r.impact.toFixed(3)}</span>
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}
