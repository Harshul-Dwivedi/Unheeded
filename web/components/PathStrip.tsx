import Link from "next/link";
import type { RecPage } from "@/lib/api";
import { place } from "@/lib/format";

type Node = { label: string; id: string; href?: string; text: string; signal?: boolean };

/** The graph path behind one linked warning, as the query walks it: origin crash -> warning -> this crash's report -> this crash. */
export default function PathStrip({ rec, crash, ms }: { rec: RecPage; crash: { ntsb_num: string; city?: string; state?: string }; ms?: number | null }) {
  const rep = rec.repeats.find((r) => r.how !== "MENTIONED_IN" && r.accidents.includes(crash.ntsb_num));
  if (!rep) return null;
  const o = rec.origin;
  const nodes: (Node | string)[] = [
    ...(o ? [{ label: o.ntsb_num ? "Accident" : "Study", id: o.ntsb_num ?? o.display ?? "", href: o.ntsb_num ? `/accident/${o.ntsb_num}` : undefined,
               text: place(o.city, o.state) || o.display || "" } as Node, "ISSUED_FROM"] : []),
    { label: "Recommendation", id: rec.rec.srid, href: `/rec/${rec.rec.srid}`, text: "the warning" },
    rep.how, { label: "Report", id: rep.report, text: "this crash's report" },
    "ABOUT", { label: "Accident", id: crash.ntsb_num, href: `/accident/${crash.ntsb_num}`, text: place(crash.city, crash.state), signal: true },
  ];
  return (
    <div>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "8px 0" }}>
        {nodes.map((n, i) => typeof n === "string" ? (
          <span key={i} className="mono" style={{ fontSize: 10, color: "var(--sub)", padding: "0 8px", whiteSpace: "nowrap" }}>
            {i === 1 && o ? "←" : ""}{n}{i === 1 && o ? "" : " →"}
          </span>
        ) : (
          <span key={i} style={{ border: `1px solid ${n.signal ? "var(--signal)" : "var(--ink)"}`, padding: "6px 10px", minWidth: 0 }}>
            <span className="k" style={{ display: "block", fontSize: 9 }}>{n.label}</span>
            {n.href ? <Link href={n.href} className="mono" style={{ fontSize: 13 }}>{n.id}</Link> : <span className="mono" style={{ fontSize: 13 }}>{n.id}</span>}
            <span style={{ display: "block", fontSize: 12, color: "var(--sub)" }}>{n.text}</span>
          </span>
        ))}
      </div>
      <p style={{ margin: "10px 0 0", fontSize: 12, color: "var(--sub)" }}>
        Recommendation -[{rep.how}]→ Report -[ABOUT]→ Accident: the two hops the headline query walks for every linked warning{ms != null ? `, all of them in ${ms} ms` : ""}.
      </p>
    </div>
  );
}
