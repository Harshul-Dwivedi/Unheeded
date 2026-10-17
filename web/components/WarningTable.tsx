"use client";
import Link from "next/link";
import { useState } from "react";
import type { LinkedWarning } from "@/lib/api";
import { asked, openFor, times, year } from "@/lib/format";
import Status from "./Status";

const GRID = "96px minmax(0, 1fr) 130px 70px 180px 180px";

/** The linked-warnings table (Investigate + Accident), with the hover/focus preview from the prototype. */
export default function WarningTable({ rows }: { rows: LinkedWarning[] }) {
  const [hover, setHover] = useState<string | null>(null);
  return (
    <div>
      <div style={{ display: "flex", flexDirection: "column" }}>
        <div className="k wt-head" style={{ display: "grid", gridTemplateColumns: GRID, gap: 16, padding: "10px 0", borderBottom: "1px solid var(--ink)" }}>
          <span>Warning</span><span>What it asked</span><span>Came from</span><span>Open for</span><span>On the crash date</span><span>Status today</span>
        </div>
        {rows.map((w) => (
          <div key={w.srid} className="wt-row" style={{ position: "relative", display: "grid", gridTemplateColumns: GRID, gap: 16, padding: "14px 0", borderBottom: "1px solid var(--rule2)", alignItems: "baseline", fontSize: 14 }}>
            <Link href={`/rec/${w.srid}`} className="mono" style={{ fontWeight: 500 }}
              onMouseEnter={() => setHover(w.srid)} onMouseLeave={() => setHover(null)} onFocus={() => setHover(w.srid)} onBlur={() => setHover(null)}>
              {w.srid}
            </Link>
            <span>{asked(w.text)}</span>
            <span style={{ color: "var(--sub)" }}>{[w.origin_place, year(w.origin_date)].filter(Boolean).join(" · ")}</span>
            <span>{openFor(w.open_days)}</span>
            <span><span className="wt-sm">On the crash date: </span><Status code={w.code_then} label={w.status_then} /></span>
            <span><span className="wt-sm">Today: </span><Status code={w.code_today} label={w.status_today} /></span>
            {hover === w.srid && (
              <div role="tooltip" style={{ position: "absolute", left: 0, top: 40, zIndex: 5, width: 300, padding: "14px 16px", background: "var(--bg)", border: "1px solid var(--ink)", fontSize: 13, lineHeight: 1.5 }}>
                <div className="mono" style={{ fontSize: 12, color: "var(--sub)" }}>{w.srid} · TO {w.owners.join(", ") || "—"}</div>
                <div style={{ margin: "4px 0 6px" }}>{asked(w.text, 200)}</div>
                <div style={{ color: "var(--sub)" }}>Repeated by the NTSB {times(w.times_reiterated)}</div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
