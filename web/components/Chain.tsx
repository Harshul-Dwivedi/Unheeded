import Link from "next/link";
import type { RecPage } from "@/lib/api";
import { date, place, year } from "@/lib/format";

const box = (kind: "fact" | "warning" | "crash"): React.CSSProperties => ({
  position: "absolute", left: -6, top: 6, width: 11, height: 11, boxSizing: "border-box",
  ...(kind === "fact" ? { background: "var(--bg)", border: "1px solid var(--ink)" } : { background: kind === "crash" ? "var(--signal)" : "var(--ink)" }),
});

/** Warning chain timeline (margin column): origin -> issued -> other linked crashes -> this crash. */
export default function Chain({ rec, crash }: { rec: RecPage; crash: { ntsb_num: string; city?: string; state?: string; event_date: number; fatal?: number } }) {
  const others = rec.linked_crashes.filter((c) => c.ntsb_num !== crash.ntsb_num && c.event_date < crash.event_date);
  const killed = others.reduce((n, c) => n + (c.fatal ?? 0), 0);
  const o = rec.origin;
  const owner = rec.owners.map((x) => x.acronym || x.name).join(", ");
  return (
    <div style={{ display: "flex", flexDirection: "column", borderLeft: "1px solid var(--ink)", marginLeft: 5 }}>
      {o && (
        <div style={{ position: "relative", padding: "0 0 22px 22px" }}>
          <span style={box("fact")} />
          <div style={{ fontSize: 12, color: "var(--sub)" }}>{o.event_date ? `${date(o.event_date)} · origin accident` : "origin study"}</div>
          <div>{o.ntsb_num ? <Link href={`/accident/${o.ntsb_num}`}>{place(o.city, o.state) || o.ntsb_num}</Link> : o.display}{o.fatal ? ` · ${o.fatal} killed` : ""}</div>
        </div>
      )}
      <div style={{ position: "relative", padding: "0 0 22px 22px" }}>
        <span style={box("warning")} />
        <div style={{ fontSize: 12, color: "var(--sub)" }}>{year(rec.rec.issued_date)} · warning issued{owner ? ` to the ${owner}` : ""}</div>
        <Link href={`/rec/${rec.rec.srid}`} className="mono">{rec.rec.srid}</Link>
      </div>
      {others.length > 0 && (
        <div style={{ position: "relative", padding: "0 0 22px 22px" }}>
          <span style={box("fact")} />
          <div style={{ fontSize: 12, color: "var(--sub)" }}>{year(others[0].event_date)}{others.length > 1 ? `–${year(others[others.length - 1].event_date)}` : ""}</div>
          <div>Linked to {others.length} earlier crash{others.length > 1 ? "es" : ""}, {killed} killed</div>
        </div>
      )}
      <div style={{ position: "relative", padding: "0 0 0 22px" }}>
        <span style={box("crash")} />
        <div className="sig" style={{ fontSize: 12 }}>{date(crash.event_date)} · still open</div>
        <div className="serif" style={{ fontSize: 22 }}>{place(crash.city, crash.state)}{crash.fatal != null ? ` · ${crash.fatal} killed` : ""}</div>
      </div>
    </div>
  );
}
