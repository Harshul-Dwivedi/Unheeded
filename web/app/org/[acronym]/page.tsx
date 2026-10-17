import Link from "next/link";
import { notFound } from "next/navigation";
import ShowMore from "@/components/ShowMore";
import WatchButton from "@/components/WatchButton";
import { api, ApiError, type OrgPage } from "@/lib/api";
import { addressee, asked, year } from "@/lib/format";

const GRID = "104px minmax(0, 1fr) 170px 60px 80px";

export default async function Organisation({ params }: { params: Promise<{ acronym: string }> }) {
  const { acronym } = await params;
  const name = decodeURIComponent(acronym);
  const d = await api<OrgPage>(`/api/org/${encodeURIComponent(name)}`).catch((e) => { if (e instanceof ApiError && e.status === 404) notFound(); throw e; });
  const n = (code: string) => d.counts.find((c) => c.status === code)?.n ?? 0;
  const full = addressee(d.unacceptable[0]?.text) || d.name;
  const periods = Object.entries(d.unacceptable_by_issue_period);
  const max = Math.max(...periods.map(([, v]) => v), 1);
  const rows = d.unacceptable.map((r) => (
    <div key={r.srid} className="row" style={{ display: "grid", gridTemplateColumns: GRID, gap: 16, padding: "13px 0", borderTop: 0, borderBottom: "1px solid var(--rule2)" }}>
      <Link href={`/rec/${r.srid}`} className="mono">{r.srid}</Link><span>{asked(r.text, 90)}</span>
      <span style={{ color: "var(--sub)" }}>{r.origin ?? "—"}</span><span className="mono" style={{ color: "var(--sub)" }}>{year(r.issued)}</span>
      <span>{r.times_reiterated ? `${r.times_reiterated}×` : "—"}</span>
    </div>
  ));
  return (
    <main className="wrap main">
      <div>
        <nav className="crumb" aria-label="Breadcrumb"><Link href="/">Investigate</Link><span>›</span><span style={{ color: "var(--ink)" }}>{d.owner}</span></nav>
        <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "flex-end", gap: 16, marginTop: 20 }}>
          <div>
            <div className="k">Organisation · addressee of {d.addressed?.toLocaleString("en-US") ?? "?"} aviation recommendations</div>
            <h1 className="h1" style={{ fontSize: 52 }}>{full}</h1>
          </div>
          <WatchButton kind="Organization" id={d.owner} label="Watch this organisation" />
        </div>
      </div>
      <div className="facts sec">
        <div className="fact"><div className="fl">Open today</div><div className="fv">{d.total_open}</div></div>
        <div className="fact"><div className="fl">■ Open, rated unacceptable</div><div className="fv" style={{ color: "var(--signal)" }}>{n("OUA")}</div></div>
        <div className="fact"><div className="fl">□ Open, rated acceptable</div><div className="fv">{n("OAA") + n("OAAR")}</div></div>
        <div className="fact"><div className="fl">Initial response received</div><div className="fv">{n("ORR")}</div></div>
        <div className="fact"><div className="fl">Awaiting a response</div><div className="fv">{n("OAR")}</div></div>
      </div>
      <div className="cols">
        <section className="sec" style={{ flex: "999 1 640px", minWidth: 0 }}>
          <div className="k" style={{ marginBottom: 4 }}>Rated unacceptable and still open · oldest first</div>
          <div style={{ overflowX: "auto" }}>
            <div style={{ minWidth: 640, display: "flex", flexDirection: "column", fontSize: 14 }}>
              <div className="k" style={{ display: "grid", gridTemplateColumns: GRID, gap: 16, padding: "10px 0", borderBottom: "1px solid var(--ink)" }}>
                <span>Warning</span><span>What it asks</span><span>Came from</span><span>Issued</span><span>Repeated</span>
              </div>
              <ShowMore items={rows} n={10} label="rated unacceptable" />
            </div>
          </div>
        </section>
        <aside className="ca">
          <section className="sec">
            <div className="k">When the unacceptable ones were issued</div>
            <p style={{ margin: "6px 0 18px", fontSize: 13, color: "var(--sub)" }}>{n("OUA")} open recommendations, by five-year period</p>
            <div style={{ display: "flex", flexDirection: "column", gap: 12, fontSize: 13 }}>
              {periods.map(([p, v]) => (
                <div key={p} className="bar"><span className="mono" style={{ color: "var(--sub)" }}>{p}</span>
                  <div style={{ height: 12, width: `${Math.max(3, Math.round((v / max) * 100))}%`, background: "var(--ink)" }} /><span style={{ textAlign: "right" }}>{v}</span></div>
              ))}
            </div>
          </section>
        </aside>
      </div>
    </main>
  );
}
