"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { loadApi, peekApi } from "@/lib/cache";
import { asked, date, place } from "@/lib/format";
import { getReview, setReview } from "@/lib/session";
import { notifyCounts, useWorkspace, WORKSPACES } from "@/lib/workspace";

type Proposal = {
  key: string; srid: string; ntsb_num: string; score: number; status: string; evidence: Record<string, number>;
  rec?: { text: string; status: string }; accident?: { city?: string; state?: string; event_date: number; fatal?: number };
};
type Review = {
  proposals: Proposal[]; decided: Proposal[]; weights: Record<string, number>; default_weights: Record<string, number>;
  rules: { signals: Record<string, { hits: number; misses: number }>; decided: number; confirmed: number }; precision: number | null; fitted: boolean;
};
type EvalCard = { eval2?: { average: Record<string, { "hit@10": number; mrr: number }> } };

const SIGNALS: Record<string, string> = {
  text: "Probable cause reads like the warning", findings: "Same finding codes as the warning's origin crash",
  events: "Same event type and phase of flight", family: "Same failure family", seed: "Resembles a link you confirmed",
};

function evidence(e: Record<string, number>): string {
  const out: string[] = [];
  if (e.text != null) out.push(`text similarity ${e.text.toFixed(2)}`);
  if (e.findings) out.push(`${e.findings} shared finding code${e.findings > 1 ? "s" : ""}`);
  if (e.events) out.push(`${e.events} shared event type${e.events > 1 ? "s" : ""}`);
  if (e.family) out.push("same failure family");
  if (e.seed != null) out.push(`similarity ${e.seed.toFixed(2)} to a confirmed link`);
  return out.join(" · ");
}
const confidence = (s: number) => (s >= 1.0 ? "HIGH" : s >= 0.75 ? "MEDIUM" : "LOW");
const SHOW = 20;

export default function ReviewPage() {
  const ws = useWorkspace();
  if (!ws) return <main className="wrap main" />;
  return <ReviewBody key={ws} ws={ws} />;
}

function ReviewBody({ ws }: { ws: string }) {
  const [data, setData] = useState<Review | null>(() => getReview<Review>(ws) ?? peekApi<Review>(`/api/workspaces/${ws}/review`));
  const [card, setCard] = useState<EvalCard | null>(() => peekApi<EvalCard>("/api/eval"));
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    loadApi<Review>(`/api/workspaces/${ws}/review`).then((r) => { setReview(ws, r); setData(r); }).catch((e) => setError(e.message));
  }, [ws]);
  useEffect(load, [load]);
  useEffect(() => { loadApi<EvalCard>("/api/eval").then(setCard).catch(() => {}); }, []);

  const decide = async (p: Proposal, verdict: "confirmed" | "rejected") => {
    setBusy(p.key); setError("");
    try {
      const r = await api<Review>(`/api/workspaces/${ws}/review`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ srid: p.srid, ntsb_num: p.ntsb_num, verdict, reviewer: "reviewer" }) });
      setReview(ws, r); setData(r);
      notifyCounts();
    } catch (e) { setError((e as Error).message); }
    setBusy(null);
  };

  const undo = async (p: Proposal) => {
    setBusy(p.key); setError("");
    try {
      const r = await api<Review>(`/api/workspaces/${ws}/review/undo`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ srid: p.srid, ntsb_num: p.ntsb_num, reviewer: "reviewer" }) });
      setReview(ws, r); setData(r);
      notifyCounts();
    } catch (e) { setError((e as Error).message); }
    setBusy(null);
  };
  const recent = data ? [...data.decided].slice(0, 6) : [];
  const maxW = data ? Math.max(...Object.keys(SIGNALS).map((k) => data.weights[k] ?? 0), 1) : 1;
  const e2 = card?.eval2?.average;
  return (
    <main className="wrap main">
      <div style={{ maxWidth: 820 }}>
        <div className="k">Review queue · {data ? data.proposals.length : "…"} waiting · {ws ? WORKSPACES[ws] ?? ws : ""}</div>
        <h1 className="h1">Possible connections not in the NTSB record</h1>
        <p style={{ margin: "12px 0 0", fontSize: 16, color: "var(--sub)" }}>
          The Analyst proposes open warnings that resemble a crash. Nothing here is official until you confirm it, and every decision tunes the next search.
        </p>
        {error && <p className="sig" role="alert">{error}</p>}
      </div>
      <div className="cols">
        <div style={{ flex: "999 1 640px", minWidth: 0, display: "flex", flexDirection: "column", gap: 24 }}>
          {data && data.proposals.length === 0 && (
            <section className="sec"><p>Nothing waiting. Proposals appear here after an investigation: <Link href="/">ask about a crash</Link>.</p></section>
          )}
          {data?.proposals.slice(0, SHOW).map((p) => (
            <section key={p.key} style={{ border: "1px dashed var(--ink)", padding: "24px 28px" }}>
              <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
                <div className="k">Proposal · {confidence(p.score)} · score {p.score.toFixed(2)}</div>
                <div style={{ fontSize: 13, fontWeight: 600 }}>Unverified</div>
              </div>
              <h2 className="serif" style={{ margin: "10px 0 4px", fontSize: 26, lineHeight: 1.2, fontWeight: 500 }}>
                {place(p.accident?.city, p.accident?.state)} · {date(p.accident?.event_date)} · {p.accident?.fatal ?? 0} killed
              </h2>
              <p style={{ margin: "0 0 16px", fontSize: 14, color: "var(--sub)" }}>
                <Link href={`/accident/${p.ntsb_num}`} className="mono">{p.ntsb_num}</Link> may relate to open warning <Link href={`/rec/${p.srid}`} className="mono">{p.srid}</Link>
                {p.rec ? <> · {asked(p.rec.text, 90)}</> : null}
              </p>
              <div style={{ borderLeft: "3px solid var(--ink)", padding: "2px 0 2px 16px", fontSize: 14, marginBottom: 18 }}>
                <div>{evidence(p.evidence)}</div>
                <div style={{ color: "var(--sub)", marginTop: 4 }}>Signals: {Object.keys(p.evidence).map((k) => SIGNALS[k]?.toLowerCase() ?? k).join(" · ")}</div>
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
                <button type="button" className="b1" disabled={!!busy} onClick={() => decide(p, "confirmed")}>Confirm link</button>
                <button type="button" className="b2" disabled={!!busy} onClick={() => decide(p, "rejected")}>Reject</button>
              </div>
            </section>
          ))}
          {data && data.proposals.length > SHOW && <p style={{ color: "var(--sub)", fontSize: 14 }}>{data.proposals.length - SHOW} more, lower-scoring proposals not shown.</p>}
          {recent.length > 0 && (
            <section className="sec">
              <div className="k" style={{ marginBottom: 6 }}>Recently decided · undo keeps a record of the change</div>
              {recent.map((p) => (
                <div key={p.key} className="row" style={{ display: "grid", gridTemplateColumns: "110px 110px minmax(0, 1fr) 100px 60px", gap: 12, padding: "10px 0", fontSize: 14, alignItems: "baseline",
                  ...(p.status === "rejected" ? { color: "var(--sub)" } : {}) }}>
                  <Link href={`/rec/${p.srid}`} className="mono">{p.srid}</Link><Link href={`/accident/${p.ntsb_num}`} className="mono">{p.ntsb_num}</Link>
                  <span>{place(p.accident?.city, p.accident?.state)} · {date(p.accident?.event_date)}</span>
                  <span style={{ fontWeight: p.status === "confirmed" ? 600 : 400 }}>{p.status === "confirmed" ? "Confirmed by a reviewer" : "Rejected by a reviewer"}</span>
                  <button type="button" className="b3" style={{ minHeight: 0, fontSize: 13 }} disabled={!!busy} onClick={() => undo(p)}>Undo</button>
                </div>
              ))}
            </section>
          )}
        </div>
        <aside className="ca">
          <section className="sec">
            <div className="k">What the agent has learned</div>
            <p style={{ margin: "6px 0 18px", fontSize: 13, color: "var(--sub)" }}>
              {!data ? "Loading…"
                : data.fitted ? `Fitted to this workspace's ${data.rules.decided} decisions (logistic regression, pulled toward the defaults in brackets)`
                : `Defaults until there are 10 decisions with both outcomes (${data.rules.decided} so far)`}
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: 16, fontSize: 14 }}>
              {data && Object.entries(SIGNALS).map(([k, label]) => {
                const w = data.weights[k] ?? 0, d = data.default_weights[k] ?? 0;
                return (
                  <div key={k}>
                    <div className="w"><span>{label}</span><span className="mono">{w.toFixed(2)} <span style={{ color: "var(--sub)" }}>({d.toFixed(2)})</span></span></div>
                    <div className="track"><div style={{ height: 6, width: `${Math.round((w / maxW) * 100)}%`, background: w >= d ? "var(--ink)" : "var(--muted)" }} /></div>
                  </div>
                );
              })}
            </div>
          </section>
          <section className="sec">
            <div className="k" style={{ marginBottom: 12 }}>Precision</div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))" }}>
              <div className="fact"><div style={{ fontSize: 12, color: "var(--sub)" }}>Your confirmations</div>
                <div className="serif" style={{ fontSize: 26 }}>{data?.precision != null ? `${Math.round(data.precision * 100)}%` : "—"}</div>
                <div style={{ fontSize: 12, color: "var(--sub)" }}>{data ? `${data.rules.confirmed} of ${data.rules.decided} decided` : ""}</div></div>
              <div className="fact"><div style={{ fontSize: 12, color: "var(--sub)" }}>Eval 2 · gold link in top 10</div>
                <div className="serif" style={{ fontSize: 26 }}>{e2 ? `${e2.cold["hit@10"]}% → ${e2.warm["hit@10"]}%` : "—"}</div>
                <div style={{ fontSize: 12, color: "var(--sub)" }}>cold → after feedback, hidden half</div></div>
            </div>
            <p style={{ margin: "12px 0 0", fontSize: 12, color: "var(--sub)" }}>
              In Eval 2 the gain comes from confirmed links reused as seeds. Fitted weights barely change ranking: on this data the default mix of signals is already about the best linear one. <Link href="/method">Method</Link>
            </p>
          </section>
        </aside>
      </div>
    </main>
  );
}
