import Link from "next/link";
import { notFound } from "next/navigation";
import Status from "@/components/Status";
import Thread, { type ThreadEvent } from "@/components/Thread";
import WatchButton from "@/components/WatchButton";
import { api, ApiError, type RecPage } from "@/lib/api";
import { addressee, date, mark, place } from "@/lib/format";

const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const monthYear = (d: number) => `${MON[(Math.floor(d / 100) % 100) - 1]} ${Math.floor(d / 10000)}`;
const firstSentence = (t?: string) => {
  const s = (t ?? "").replace(/\s+/g, " ").trim();
  const m = /^(.{20,220}?[.;])(\s|$)/.exec(s);
  return m ? m[1] : s.slice(0, 220) + (s.length > 220 ? "…" : "");
};
const today = () => { const n = new Date(); return n.getFullYear() * 10000 + (n.getMonth() + 1) * 100 + n.getDate(); };
const years = (a: number, b: number) => {
  const d = (x: number) => Date.UTC(Math.floor(x / 10000), Math.floor(x / 100) % 100 - 1, x % 100);
  const y = (d(b) - d(a)) / (365.25 * 864e5);
  return y < 1 ? "< 1 year" : `~${Math.round(y)} years`;
};

export default async function Recommendation({ params }: { params: Promise<{ srid: string }> }) {
  const { srid } = await params;
  const d = await api<RecPage>(`/api/rec/${encodeURIComponent(srid)}`).catch((e) => { if (e instanceof ApiError && e.status === 404) notFound(); throw e; });
  const r = d.rec;
  const owner = d.owners.map((o) => o.acronym || o.name).filter(Boolean)[0] ?? "Owner";
  const m = mark(r.status_code, r.status_label);
  const unacceptableSince = r.status_code === "OUA" ? [...d.history].reverse().find((h) => h.code === "OUA")?.valid_from : undefined;
  const killed = d.linked_crashes.reduce((n, c) => n + (c.fatal ?? 0), 0);
  const linkedIds = new Set(d.linked_crashes.map((c) => c.ntsb_num));
  const crashCity = new Map(d.linked_crashes.map((c) => [c.ntsb_num, place(c.city, c.state)]));

  const events: ThreadEvent[] = [
    { date: r.issued_date, when: r.issued_from === "id" ? String(Math.floor(r.issued_date / 10000)) : date(r.issued_date), who: "NTSB", kind: "ntsb",
      text: d.origin?.ntsb_num ? `Issued after the ${place(d.origin.city, d.origin.state) || d.origin.ntsb_num} accident` : `Issued in ${d.origin?.display ?? "a safety study"}` },
    ...d.letters.filter((l) => l.date && !["Recommendation Reiteration", "Report Reclassification", "Recommendation Mention"].includes(l.type ?? ""))
      .map((l) => ({ date: l.date!, when: date(l.date), who: l.from_org || "—", kind: l.from_org === "NTSB" ? "ntsb" : "owner", text: firstSentence(l.summary) || (l.type ?? "") })),
    ...d.repeats.filter((x) => x.date).map((x) => {
      const crash = x.accidents.find((a) => linkedIds.has(a)) ?? x.accidents[0];
      const verb = x.how === "REITERATED_IN" ? "Repeated" : x.how === "RECLASSIFIED_IN" ? "Reclassified" : "Mentioned";
      return { date: x.date!, when: date(x.date), who: x.how === "MENTIONED_IN" ? "MENTIONED" : "REPEATED", kind: x.how === "MENTIONED_IN" ? "mention" : "crash",
               text: `${verb} in report ${x.report}${crash ? `, ${crashCity.get(crash) ?? crash}` : ""}` };
    }),
    ...d.linked_crashes.map((c) => ({ date: c.event_date, when: date(c.event_date), who: "CRASH", kind: "crash", hot: true,
                                      text: `${place(c.city, c.state)} crash · ${c.fatal} killed` })),
  ].sort((a, b) => a.date - b.date);

  return (
    <main className="wrap main">
      <div>
        <nav className="crumb" aria-label="Breadcrumb">
          <Link href="/">Investigate</Link><span>›</span>
          {d.origin?.ntsb_num && <><Link href={`/accident/${d.origin.ntsb_num}`}>{d.origin.ntsb_num}</Link><span>›</span></>}
          <span style={{ color: "var(--ink)" }}>{r.srid}</span>
        </nav>
        <div className="cols" style={{ marginTop: 20, gap: 48 }}>
          <div style={{ flex: "999 1 620px", minWidth: 0 }}>
            <div className="k">Recommendation · to the {addressee(r.text) || owner}</div>
            <div style={{ display: "flex", flexWrap: "wrap", alignItems: "baseline", gap: "8px 22px", marginTop: 8 }}>
              <h1 className="h1" style={{ margin: 0, fontSize: 64, lineHeight: 1 }}>{r.srid}</h1>
              {r.status_code === "OUA" ? <span className="sig" title={r.status_label}>■ Open — rated unacceptable by the NTSB</span> : <Status code={r.status_code} label={r.status_label} />}
            </div>
            <p className="serif" style={{ margin: "20px 0 0", fontSize: 22, lineHeight: 1.55, color: "var(--ink2)" }}>“{r.text.replace(/^TO\s+[^:]{1,240}:\s*/i, "")}”</p>
            <p style={{ margin: "14px 0 0", fontSize: 14, color: "var(--sub)" }}>
              {d.origin?.ntsb_num
                ? <>Issued after the <Link href={`/accident/${d.origin.ntsb_num}`}>{place(d.origin.city, d.origin.state)}</Link> accident · {date(d.origin.event_date)}{d.origin.fatal ? ` · ${d.origin.fatal} killed` : ""}</>
                : <>Issued in {d.origin?.display ?? "a safety study"}</>}
              {(d.supersedes.length > 0 || d.superseded_by.length > 0) && <> · {d.supersedes.length > 0 && <>supersedes {d.supersedes.map((x, i) => <span key={x.srid}>{i ? ", " : ""}<Link href={`/rec/${x.srid}`} className="mono">{x.srid}</Link></span>)}</>}
                {d.superseded_by.length > 0 && <>{d.supersedes.length ? "; " : ""}superseded by {d.superseded_by.map((x, i) => <span key={x.srid}>{i ? ", " : ""}<Link href={`/rec/${x.srid}`} className="mono">{x.srid}</Link></span>)}</>}</>}
            </p>
          </div>
          <aside className="sec" style={{ flex: "1 1 260px", display: "flex", flexDirection: "column", gap: 12 }}>
            {d.owners.map((o) => (
              <div key={o.name} style={{ display: "flex", justifyContent: "space-between", gap: 10, paddingBottom: 10, borderBottom: "1px solid var(--rule2)" }}>
                <span style={{ color: "var(--sub)" }}>Owner</span><Link href={`/org/${encodeURIComponent(o.acronym || o.name)}`} style={{ fontWeight: 600 }}>{o.acronym || o.name}</Link>
              </div>
            ))}
            {r.letter_url && (
              <div style={{ display: "flex", justifyContent: "space-between", gap: 10, paddingBottom: 10, borderBottom: "1px solid var(--rule2)" }}>
                <span style={{ color: "var(--sub)" }}>Official letter</span><a href={r.letter_url.replace(/^http:/, "https:")} target="_blank" rel="noreferrer">Open (ntsb.gov)</a>
              </div>
            )}
            <WatchButton kind="Recommendation" id={r.srid} label="Watch this warning" primary />
          </aside>
        </div>
      </div>
      <div className="facts sec">
        <div className="fact"><div className="fl">{r.is_open ? "Open for" : "Was open for"}</div><div className="fv" style={{ fontSize: 34 }}>{years(r.issued_date, r.closed_date ?? today())}</div></div>
        <div className="fact"><div className="fl">Repeated by the NTSB</div><div className="fv" style={{ fontSize: 34 }}>{r.times_reiterated ? `${r.times_reiterated} time${r.times_reiterated > 1 ? "s" : ""}` : "never"}</div></div>
        <div className="fact"><div className="fl">{unacceptableSince ? "Rated unacceptable since" : "Status"}</div>
          <div className="fv" style={{ fontSize: 34, ...(m.signal ? { color: "var(--signal)" } : {}) }}>{unacceptableSince ? monthYear(unacceptableSince) : m.text.replace(/^[■□—]\s*/, "")}</div></div>
        <div className="fact"><div className="fl">Killed in linked crashes since</div><div className="fv" style={{ fontSize: 34, color: killed ? "var(--signal)" : undefined }}>{killed}</div></div>
      </div>
      <div className="cols">
        <section className="sec" style={{ flex: "999 1 620px", minWidth: 0 }}>
          <Thread events={events} owner={owner} />
        </section>
        <aside className="ca">
          <section className="sec">
            <div className="k" style={{ marginBottom: 6 }}>Linked crashes after it was issued</div>
            {d.linked_crashes.length === 0 && <p style={{ fontSize: 14 }}>None in the record.</p>}
            <div style={{ display: "flex", flexDirection: "column", fontSize: 14 }}>
              {d.linked_crashes.map((c, i) => (
                <div key={c.ntsb_num} className="row" style={{ display: "flex", justifyContent: "space-between", gap: 12, padding: "11px 0", ...(i === d.linked_crashes.length - 1 ? { borderBottom: "1px solid var(--rule2)" } : {}) }}>
                  <Link href={`/accident/${c.ntsb_num}`}>{place(c.city, c.state)} · {monthYear(c.event_date)}</Link>
                  <span className="serif" style={{ fontSize: 18, ...(i === d.linked_crashes.length - 1 && r.is_open ? { color: "var(--signal)" } : {}) }}>{c.fatal}</span>
                </div>
              ))}
            </div>
            {d.linked_crashes.length > 0 && <div style={{ fontSize: 12, color: "var(--sub)", marginTop: 8 }}>People killed. Linked by the NTSB in each crash’s report.</div>}
          </section>
          <section className="sec">
            <div className="k" style={{ marginBottom: 6 }}>Status over time</div>
            {d.history.map((h) => (
              <div key={h.valid_from + h.code} className="row" style={{ display: "grid", gridTemplateColumns: "96px minmax(0, 1fr)", gap: 12, padding: "9px 0", fontSize: 13 }}>
                <span className="mono" style={{ color: "var(--sub)" }}>{monthYear(h.valid_from)}{h.exact ? "" : "*"}</span><Status code={h.code} label={h.label} />
              </div>
            ))}
            <div style={{ fontSize: 12, color: "var(--sub)", marginTop: 8 }}>From the NTSB&apos;s classification letters.{d.history.some((h) => !h.exact) ? " * day of the change unknown." : ""}</div>
          </section>
        </aside>
      </div>
    </main>
  );
}
