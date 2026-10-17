import Link from "next/link";
import { notFound } from "next/navigation";
import Status from "@/components/Status";
import UsMap from "@/components/UsMap";
import WatchButton from "@/components/WatchButton";
import { api, ApiError, type AccidentPage } from "@/lib/api";
import { asked, date, place } from "@/lib/format";

export default async function Accident({ params }: { params: Promise<{ ntsb: string }> }) {
  const { ntsb } = await params;
  const d = await api<AccidentPage>(`/api/accident/${encodeURIComponent(ntsb)}`).catch((e) => { if (e instanceof ApiError && e.status === 404) notFound(); throw e; });
  const a = d.accident;
  const coord = a.lat != null && a.lon != null ? `${Math.abs(a.lat).toFixed(2)}° ${a.lat >= 0 ? "N" : "S"} · ${Math.abs(a.lon).toFixed(2)}° ${a.lon >= 0 ? "E" : "W"}` : null;
  return (
    <main className="wrap main">
      <div>
        <nav className="crumb" aria-label="Breadcrumb"><Link href="/">Investigate</Link><span>›</span><span style={{ color: "var(--ink)" }}>{a.ntsb_num}</span></nav>
        <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "flex-end", gap: 16, marginTop: 20 }}>
          <div style={{ flex: "1 1 600px" }}>
            <div className="k">Accident · {a.ntsb_num}{d.reports.length ? ` · Report ${d.reports.join(", ")}` : ""}{a.partial ? " · partial record" : ""}</div>
            <h1 className="h1">{place(a.city, a.state) || a.ntsb_num}</h1>
          </div>
          <WatchButton kind="Accident" id={a.ntsb_num} label="Watch this accident" />
        </div>
      </div>
      <div className="facts sec">
        <div className="fact"><div className="fl">Date</div><div className="fv" style={{ fontSize: 32 }}>{date(a.event_date)}</div></div>
        <div className="fact"><div className="fl">Killed</div><div className="fv" style={{ fontSize: 32, color: "var(--signal)" }}>{a.fatal ?? "—"}</div></div>
        <div className="fact"><div className="fl">Highest injury</div><div className="fv" style={{ fontSize: 32 }}>{a.injury_level ?? "—"}</div></div>
        <div className="fact"><div className="fl">Linked warnings open</div><div className="fv" style={{ fontSize: 32 }}>{d.linked.length}</div></div>
      </div>
      <div className="cols">
        <div className="cm">
          {a.probable_cause && (
            <section className="sec">
              <div className="k">Probable cause · as determined by the NTSB</div>
              <blockquote className="serif" style={{ margin: "16px 0 0", padding: "0 0 0 22px", borderLeft: "3px solid var(--ink)", fontSize: 22, lineHeight: 1.55, color: "var(--ink2)" }}>
                {a.probable_cause}
              </blockquote>
            </section>
          )}
          <section className="sec">
            <div className="k" style={{ marginBottom: 4 }}>Linked warnings open that day</div>
            {d.linked.length === 0 ? (
              <p>None. {!d.absence?.no_linked_warning ? "" : d.absence.has_report
                ? `Proven: no warning repeated or reclassified in ${d.absence.reports.join(", ")} was issued before the crash and still open that day (${d.absence.ms.toFixed(1)} ms).`
                : "This crash has no NTSB report in the data, so there was nothing to search. This is not evidence either way."}</p>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", fontSize: 14 }}>
                {d.linked.map((w) => (
                  <div key={w.srid} className="row" style={{ display: "grid", gridTemplateColumns: "104px minmax(0, 1fr) 240px", gap: 16, padding: "13px 0" }}>
                    <Link href={`/rec/${w.srid}`} className="mono">{w.srid}</Link><span>{asked(w.text, 90)}</span>
                    <span><Status code={w.code_then} label={w.status_then} /><br /><span style={{ fontSize: 12, color: "var(--sub)" }}>today: <Status code={w.code_today} label={w.status_today} /></span></span>
                  </div>
                ))}
              </div>
            )}
          </section>
          {d.mentions.length > 0 && (
            <section className="sec">
              <div className="k" style={{ marginBottom: 4 }}>Mentioned in the report, not linked</div>
              <p style={{ margin: "4px 0 8px", fontSize: 13, color: "var(--sub)" }}>The NTSB discussed these but did not repeat or reclassify them in this report. A mention is not a link.</p>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 18px", fontSize: 14 }}>
                {d.mentions.map((m) => <Link key={m.srid} href={`/rec/${m.srid}`} className="mono">{m.srid}</Link>)}
              </div>
            </section>
          )}
        </div>
        <aside className="ca">
          {coord && (
            <section className="sec">
              <div className="k" style={{ marginBottom: 14 }}>Location</div>
              <UsMap points={[{ lat: a.lat, lon: a.lon, label: a.city, fatal: a.fatal }]} alt={`Map of the United States, ${a.city ?? "the crash site"} marked`} />
              <p className="mono" style={{ margin: "10px 0 0", fontSize: 12, color: "var(--sub)" }}>{coord}{a.state === "AK" ? " · Alaska shown as inset" : a.state === "HI" ? " · Hawaii shown as inset" : ""}</p>
            </section>
          )}
          {d.similar.length > 0 && (
            <section className="sec">
              <div className="k" style={{ marginBottom: 6 }}>Similar probable causes</div>
              {d.similar.map((s) => (
                <div key={s.ntsb_num} className="row" style={{ display: "flex", justifyContent: "space-between", gap: 12, padding: "11px 0", fontSize: 14 }}>
                  <Link href={`/accident/${s.ntsb_num}`}>{s.city} · {date(s.event_date)}</Link><span className="mono" style={{ color: "var(--sub)" }}>{s.similarity.toFixed(2)}</span>
                </div>
              ))}
              <div style={{ fontSize: 12, color: "var(--sub)", marginTop: 8 }}>Similarity of the probable-cause text (0 to 1). Not an NTSB finding.</div>
            </section>
          )}
        </aside>
      </div>
    </main>
  );
}
