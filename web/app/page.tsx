"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import Chain from "@/components/Chain";
import PathStrip from "@/components/PathStrip";
import WarningTable from "@/components/WarningTable";
import { api, type AccidentPage, type Investigation, type RecPage, type RunResult, type Step } from "@/lib/api";
import { prefetchDashboard } from "@/lib/cache";
import { count, date, LINKED_DEF, lowerCount, NOT_PREVENTION, place } from "@/lib/format";
import { getInvestigate, setInvestigate, type InvestigateSnap } from "@/lib/session";
import { notifyCounts, useWorkspace } from "@/lib/workspace";

const DEFAULT_Q = "Was the Bering Air crash near Nome warned about?";

export default function Page() {
  return <Suspense><Investigate /></Suspense>;
}

function Investigate() {
  const params = useSearchParams();
  const ws = useWorkspace();
  if (!ws) return <main className="wrap main" />;
  return <InvestigateRun key={ws} ws={ws} qParam={params.get("q")} />;
}

function InvestigateRun({ ws, qParam }: { ws: string; qParam: string | null }) {
  const router = useRouter();
  const snap = getInvestigate(ws);
  const [q, setQ] = useState(qParam ?? snap?.q ?? DEFAULT_Q);
  const [asked, setAsked] = useState(snap?.asked ?? "");
  const [phase, setPhase] = useState<InvestigateSnap["phase"]>(snap?.phase ?? "idle");
  const [steps, setSteps] = useState<(Step & { summary?: string })[]>(snap?.steps ?? []);
  const [result, setResult] = useState<RunResult | null>(snap?.result ?? null);
  const [acc, setAcc] = useState<AccidentPage | null>(snap?.acc ?? null);
  const [chain, setChain] = useState<RecPage | null>(snap?.chain ?? null);
  const [error, setError] = useState(snap?.error ?? "");
  const [thread, setThread] = useState<string | null>(snap?.thread ?? null);
  const es = useRef<EventSource | null>(null);
  const started = useRef(false);

  const loadAccident = (res: RunResult) => {
    const n = res.facts.accident?.ntsb_num;
    if (!n || res.status === "not_found") return;
    api<AccidentPage>(`/api/accident/${n}`).then((a) => {
      setAcc(a);
      const top = [...a.linked].sort((x, y) => y.times_reiterated - x.times_reiterated)[0];
      if (top) api<RecPage>(`/api/rec/${top.srid}`).then(setChain).catch(() => {});
    }).catch(() => {});
  };

  const listen = (events: string) => {
    es.current?.close();
    const src = new EventSource(events);
    es.current = src;
    src.addEventListener("step", (e) => {
      const step = JSON.parse((e as MessageEvent).data);
      setSteps((s) => (s.some((x) => x.n === step.n) ? s : [...s, step]));
    });
    src.addEventListener("result", (e) => {
      const res: RunResult = JSON.parse((e as MessageEvent).data);
      setResult(res); setPhase("done"); notifyCounts();
      loadAccident(res);
      prefetchDashboard(ws, true, router);
    });
    src.addEventListener("error", (e) => {
      const data = (e as MessageEvent).data;
      if (data) { setError(JSON.parse(data).message); setPhase("error"); }
    });
    src.addEventListener("end", () => src.close());
  };

  const start = async (question: string) => {
    if (question.trim().length < 3) return;
    es.current?.close();
    setAsked(question); setPhase("running"); setSteps([]); setResult(null); setAcc(null); setChain(null); setError(""); setThread(null);
    prefetchDashboard(ws, false, router);
    try {
      const r = await api<{ thread: string; events: string }>("/api/runs", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question, workspace: ws }),
      });
      setThread(r.thread);
      listen(r.events);
    } catch (e) {
      setError(String((e as Error).message)); setPhase("error");
    }
  };

  useEffect(() => {  // deep link: /?q=... runs straight away, unless this tab already has that run
    if (!qParam || started.current) return;
    started.current = true;
    if (snap && snap.asked === qParam && snap.phase !== "idle") return;
    start(qParam);
  }, [qParam]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {  // a run still in flight when you left Investigate: reconnect (server replays events)
    if (phase === "running" && thread) listen(`/api/runs/${encodeURIComponent(thread)}/events`);
    if (phase === "done" && result && !acc) loadAccident(result);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => es.current?.close(), []);
  useEffect(() => {
    setInvestigate({ ws, q, asked, phase, steps, result, acc, chain, error, thread });
  }, [ws, q, asked, phase, steps, result, acc, chain, error, thread]);
  useEffect(() => {  // Briefs may approve/reject while this page is unmounted; refresh the CTA from the graph
    const id = result?.investigation;
    if (!ws || !id || phase !== "done") return;
    const sync = () => {
      api<Investigation>(`/api/workspaces/${ws}/investigations/${id}`).then((inv) => {
        const b = inv.briefs?.[0];
        if (b) setResult((r) => (r && r.investigation === id ? { ...r, brief: { ...r.brief, ...b } } : r));
      }).catch(() => {});
    };
    sync();
    window.addEventListener("unheeded-counts", sync);
    return () => window.removeEventListener("unheeded-counts", sync);
  }, [ws, phase, result?.investigation]);

  return (
    <main className="wrap main">
      <form onSubmit={(e) => { e.preventDefault(); start(q); }} style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end" }}>
        <label className="k" style={{ flex: "999 1 480px", display: "flex", flexDirection: "column", gap: 8 }}>
          Ask the record
          <input type="text" className="qin" value={q} onChange={(e) => setQ(e.target.value)} maxLength={400} />
        </label>
        <button type="submit" className="b1" disabled={!ws || phase === "running"} style={{ flex: "1 1 160px", height: 56, justifyContent: "center", fontSize: 16 }}>
          {phase === "running" ? "Investigating…" : "Investigate"}
        </button>
      </form>
      {phase === "idle" && <Intro />}
      {phase === "error" && <section className="sec"><div className="k">Something went wrong</div><p>{error}</p></section>}
      {phase === "running" && <Streaming question={asked} steps={steps} />}
      {phase === "done" && result && <Answer result={result} acc={acc} chain={chain} steps={steps} />}
    </main>
  );
}

function Intro() {
  const [h, setH] = useState<{ open: number; open_unacceptable: number } | null>(null);
  useEffect(() => { api<{ open: number; open_unacceptable: number }>("/api/health").then(setH).catch(() => {}); }, []);
  const examples = ["Was the Bering Air crash near Nome warned about?", "Was the Kekaha Hawaii crash in 2019 warned about?",
                    "What happened with A-16-035?", "What does the FAA still have open?"];
  return (
    <section className="sec" style={{ maxWidth: 900 }}>
      <div className="k">What this answers</div>
      <p className="serif" style={{ margin: "10px 0 16px", fontSize: 24, lineHeight: 1.35 }}>
        For a US aviation crash: had the NTSB already warned about this, and was that warning still open on the day it happened?
      </p>
      {h && (
        <p style={{ margin: "0 0 18px", color: "var(--sub)" }}>
          Today <b style={{ color: "var(--ink)" }}>{h.open.toLocaleString("en-US")}</b> US aviation safety recommendations are open;
          the NTSB rates <b style={{ color: "var(--signal)" }}>{h.open_unacceptable}</b> of them Open—Unacceptable Response.
        </p>
      )}
      <div style={{ display: "flex", flexDirection: "column" }}>
        {examples.map((x) => <Link key={x} href={`/?q=${encodeURIComponent(x)}`} className="row" style={{ padding: "12px 0", textDecoration: "none" }}
          onClick={(e) => { e.preventDefault(); window.location.href = `/?q=${encodeURIComponent(x)}`; }}>{x}</Link>)}
      </div>
    </section>
  );
}

function Streaming({ question, steps }: { question: string; steps: (Step & { summary?: string })[] }) {
  const last = steps[steps.length - 1];
  const found = steps.find((s) => s.tool === "find_open_warnings");
  const partial = !steps.some((s) => s.tool === "resolve_accident" || s.tool === "trace_chain" || s.tool === "owner_backlog") ? "Nothing found yet."
    : !found ? "Found so far: the record. Checking linked warnings…"
    : `Found so far: ${found.summary}. Looking for connections the NTSB did not make…`;
  return (
    <>
      <div>
        <div className="k">Investigating</div>
        <p className="serif" style={{ margin: "8px 0 0", fontSize: 26 }}>“{question}”</p>
      </div>
      <section className="sec" style={{ maxWidth: 980 }}>
        <div className="k" aria-live="polite">Step {steps.length + 1} · results appear as they arrive</div>
        <div style={{ marginTop: 10 }}>
          {steps.map((s) => (
            <div key={s.n} className="step" style={{ color: "var(--ink)" }}>
              <span>✓</span><span style={{ color: "var(--sub)", textTransform: "capitalize" }}>{s.agent}</span>
              <span className="mono">{s.tool}</span><span style={{ color: "var(--sub)" }}>{s.summary}</span>
              <span className="mono" style={{ textAlign: "right", color: "var(--muted)" }}>{s.ms != null ? `${s.ms} ms` : ""}</span>
            </div>
          ))}
          <div className="step" style={{ color: "var(--ink)", fontWeight: 600 }}>
            <span>→</span><span style={{ color: "var(--sub)" }}>{last ? nextAgent(last.agent) : "Orchestrator"}</span><span className="mono" />
            <span style={{ color: "var(--sub)" }}>Running…</span><span />
          </div>
        </div>
      </section>
      <section className="sec" style={{ maxWidth: 980 }}>
        <div className="k">Answer</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 16 }} aria-hidden="true">
          <div className="skel" style={{ width: "82%", height: 28 }} /><div className="skel" style={{ width: "56%", height: 28 }} />
          <div className="skel" style={{ width: "38%", marginTop: 8 }} />
        </div>
        <p style={{ margin: "18px 0 0", fontSize: 13, color: "var(--sub)" }}>{partial}</p>
      </section>
    </>
  );
}

const nextAgent = (a: string) => ({ orchestrator: "Scout", scout: "Analyst", analyst: "Clerk", clerk: "Gate" } as Record<string, string>)[a] ?? "Gate";

function Answer({ result, acc, chain, steps }: { result: RunResult; acc: AccidentPage | null; chain: RecPage | null; steps: Step[] }) {
  const { facts } = result;
  const plan = facts.plan;
  const proposals = result.calls.filter((c) => c.tool === "find_candidates").flatMap((c) => c.result.candidates ?? []);
  let body: React.ReactNode;
  if (result.status === "not_understood" || result.status === "not_found") {
    body = (
      <section className="sec" style={{ maxWidth: 900 }}>
        <div className="k">No answer</div>
        <h1 className="h1" style={{ fontSize: 36 }}>{result.status === "not_found" ? "No crash in the record matches that." : "I could not tell what the question is about."}</h1>
        {!!facts.did_you_mean?.length && (
          <div style={{ marginTop: 18 }}>
            <div className="k">Did you mean</div>
            {facts.did_you_mean.map((m) => (
              <a key={m.ntsb_num} className="row" href={`/?q=${encodeURIComponent(`Was the crash ${m.ntsb_num} warned about?`)}`} style={{ display: "block", padding: "12px 0" }}>
                <span className="mono">{m.ntsb_num}</span> · {place(m.city, m.state)} · {date(m.event_date)}
              </a>
            ))}
          </div>
        )}
        <p style={{ color: "var(--sub)" }}>Try an NTSB number (e.g. ANC25MA018), a place and year, a recommendation number (A-16-035) or an organisation (FAA).</p>
      </section>
    );
  } else if (plan?.intent === "crash" && facts.accident) {
    body = acc && acc.linked.length === 0 ? <NoWarning acc={acc} proposals={proposals} />
      : <CrashAnswer acc={acc} chain={chain} ms={steps.find((x) => x.tool === "find_open_warnings")?.ms} />;
  } else if (plan?.intent === "recommendation" && plan.srid) {
    body = <TextAnswer kicker={`Answer · ${plan.srid}`} text={result.brief.text} link={[`/rec/${plan.srid}`, `Open ${plan.srid}`]} />;
  } else if (plan?.intent === "owner" && plan.acronym) {
    body = <TextAnswer kicker={`Answer · ${plan.acronym}`} text={result.brief.text} link={[`/org/${plan.acronym}`, `Open the ${plan.acronym} backlog`]} />;
  }
  const tools = steps.filter((s) => !["plan", "check"].includes(s.tool));
  const agents = new Set(tools.map((s) => s.agent));
  const ms = tools.reduce((n, s) => n + (s.ms ?? 0), 0);
  return (
    <>
      {body}
      <section className="sec" style={{ display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
        <div>
          <div className="k">How we got here</div>
          <div style={{ marginTop: 4 }}>
            {tools.length} tool calls by {agents.size} agents · {ms.toFixed(0)} ms of graph queries · {proposals.length} unverified proposal{proposals.length === 1 ? "" : "s"}
          </div>
        </div>
        <div style={{ display: "flex", gap: 20 }}>
          <Link href={`/agents?inv=${result.investigation}`} className="b3" style={{ display: "inline-flex", alignItems: "center" }}>Open the trace</Link>
          {proposals.length > 0 && <Link href="/review" className="b3" style={{ display: "inline-flex", alignItems: "center" }}>Review the proposals</Link>}
        </div>
      </section>
      <ApprovalBar result={result} />
    </>
  );
}

function ApprovalBar({ result }: { result: RunResult }) {
  const st = result.brief.status;
  const v = result.brief.verdict;
  const href = `/briefs?inv=${result.investigation}`;
  const inverse = st === "awaiting_approval" || st === "blocked" || st === "draft";
  const tag = st === "approved" ? "Approved" : st === "rejected" ? "Rejected" : st === "sent_back" ? "Sent back"
    : (v?.passed && st !== "blocked") ? "Held for approval" : "Approval blocked";
  const text = st === "approved" ? `Saved to the workspace${result.brief.decided_by ? ` by ${result.brief.decided_by}` : ""}`
    : st === "rejected" ? "Nothing was published"
    : st === "sent_back" ? "Returned to Clerk · the new draft will replace this one"
    : v?.passed ? `Brief drafted · every citation checked against the graph (${v.claims_checked} claims)`
    : `${v?.blocked.length ?? 0} of ${v?.claims_checked ?? 0} claims have no supporting path`;
  const cta = st === "approved" || st === "rejected" ? "Open the brief" : st === "sent_back" ? "Open the brief"
    : v?.passed ? "Review and approve" : "See why";
  return (
    <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "12px 24px", padding: "18px 24px",
      ...(inverse ? { background: "var(--inverse)", color: "var(--inverseInk)" } : { border: "2px solid var(--ink)", color: "var(--ink)" }) }}>
      <span className="k" style={{ color: "inherit" }}>{tag}</span>
      <span style={{ flex: "1 1 320px" }}>{text}</span>
      <Link href={href} style={{ display: "inline-flex", alignItems: "center", minHeight: 44, padding: "0 20px", textDecoration: "none", fontWeight: 600,
        ...(inverse ? { background: "var(--inverseInk)", color: "var(--inverse)" } : { background: "var(--ink)", color: "var(--bg)" }) }}>
        {cta}
      </Link>
    </div>
  );
}

function CrashAnswer({ acc, chain, ms }: { acc: AccidentPage | null; chain: RecPage | null; ms?: number | null }) {
  const [prov, setProv] = useState(false);
  if (!acc) return <section className="sec"><div className="k">Answer</div><p>Loading the record…</p></section>;
  const a = acc.accident;
  const n = acc.linked.length;
  return (
    <div className="cols">
      <div className="cm">
        <section className="sec">
          <div className="k">Answer · {a.ntsb_num}</div>
          <h1 className="h1" style={{ fontSize: 40, lineHeight: 1.12 }}>
            Yes. {count(n)} warning{n === 1 ? "" : "s"} the NTSB linked to this crash {n === 1 ? "was" : "were"} still open when it happened.
          </h1>
          <p style={{ margin: "14px 0 0", color: "var(--sub)" }}>
            {date(a.event_date, true)} · {place(a.city, a.state)} · {acc.reports.map((r) => <Link key={r} href={`/accident/${a.ntsb_num}`}>Report {r}</Link>)}
          </p>
          <div className="facts" style={{ marginTop: 28 }}>
            <div className="fact"><div className="fl">Killed</div><div className="fv" style={{ color: "var(--signal)" }}>{a.fatal ?? 0}</div></div>
            <div className="fact"><div className="fl">Linked warnings open</div><div className="fv">{n}</div></div>
            <div className="fact"><div className="fl">Rated unacceptable on the crash date</div><div className="fv">{acc.rated_unacceptable_then}</div></div>
            <div className="fact"><div className="fl">Rated unacceptable today</div><div className="fv">{acc.rated_unacceptable_today}</div></div>
          </div>
          <button type="button" onClick={() => setProv(!prov)} className="b3" style={{ marginTop: 10, fontSize: 13, color: "var(--sub)" }}>
            {prov ? "Hide how we know" : "How we know these numbers"}
          </button>
          {prov && (
            <div style={{ marginTop: 8, padding: "16px 18px", background: "var(--wash)", fontSize: 13, display: "flex", flexDirection: "column", gap: 6 }}>
              <div><b>Killed</b> · investigation record {a.ntsb_num}, field FatalInjuryCount</div>
              <div><b>Linked warnings open</b> · recommendations the NTSB repeated or reclassified in {acc.reports.join(", ") || "this crash's report"}, issued before {date(a.event_date)} and not closed by then</div>
              <div><b>Rated unacceptable on the crash date</b> · NTSB status of those {lowerCount(n)} on {date(a.event_date)}, from its letters</div>
              <div><b>Rated unacceptable today</b> · current NTSB status of those {lowerCount(n)}, from the latest CAROL export</div>
              <Link href="/method">Read the full method</Link>
            </div>
          )}
        </section>
        {chain && (
          <section className="sec">
            <div className="k" style={{ marginBottom: 12 }}>The path in the graph · {chain.rec.srid}</div>
            <PathStrip rec={chain} crash={a} ms={ms} />
          </section>
        )}
        <section className="sec">
          <div className="k" style={{ marginBottom: 4 }}>The {lowerCount(n)} linked warning{n === 1 ? "" : "s"}</div>
          <WarningTable rows={acc.linked} />
          <p style={{ margin: "14px 0 0", fontSize: 13, color: "var(--sub)" }}>
            {LINKED_DEF} {NOT_PREVENTION}
          </p>
        </section>
      </div>
      <aside className="ca">
        {chain && (
          <section className="sec">
            <div className="k" style={{ marginBottom: 18 }}>Warning chain · {chain.rec.srid}</div>
            <Chain rec={chain} crash={a} />
          </section>
        )}
      </aside>
    </div>
  );
}

function NoWarning({ acc, proposals }: { acc: AccidentPage; proposals: { id: string }[] }) {
  const [showQ, setShowQ] = useState(false);
  const a = acc.accident;
  const p = acc.absence;
  const reports = p?.reports ?? acc.reports;
  const outside = p?.repeated_outside_definition ?? [];
  return (
    <>
      <section className="sec" style={{ maxWidth: 900 }}>
        <div className="k">Answer · {a.ntsb_num}</div>
        <h1 className="serif" style={{ margin: "10px 0 0", fontSize: 44, lineHeight: 1.1, fontWeight: 500, letterSpacing: "-.015em" }}>
          {reports.length ? "No. The NTSB has not linked any warning to this crash." : "No linked warning, but there is no NTSB report to check yet."}
        </h1>
        <p style={{ margin: "14px 0 0", color: "var(--sub)" }}>{date(a.event_date, true)} · {place(a.city, a.state)} · {a.fatal ?? 0} killed</p>
      </section>
      <section style={{ maxWidth: 900, border: reports.length ? "2px solid var(--ink)" : "1px solid var(--rule)", padding: "26px 30px" }}>
        <div className="k">{reports.length ? "Proof of absence" : "Nothing to search"}</div>
        <p className="serif" style={{ margin: "10px 0 20px", fontSize: 22, lineHeight: 1.45 }}>
          {reports.length
            ? `We searched the NTSB's report on this crash (${reports.join(", ")}) for every warning it repeated or reclassified. None was issued before the crash and still open that day.`
            : "A warning is linked only when the NTSB repeats or reclassifies it in the crash's own report. The data holds no report for this crash, so there was nothing to search. This is not evidence either way."}
        </p>
        <div className="facts">
          <div className="fact"><div className="fl">Reports searched</div><div className="fv">{reports.length}</div></div>
          <div className="fact"><div className="fl">Warnings repeated in them</div><div className="fv">{outside.length}</div></div>
          <div className="fact"><div className="fl">Qualifying paths</div><div className="fv">0</div></div>
          <div className="fact"><div className="fl">Query time</div><div className="fv">{p ? `${p.ms.toFixed(1)} ms` : "…"}</div></div>
        </div>
        {outside.length > 0 && (
          <div style={{ marginTop: 18 }}>
            <div className="k">Repeated in the report, but not linked</div>
            {outside.map((x) => (
              <div key={x.srid} className="row" style={{ padding: "10px 0" }}>
                <Link href={`/rec/${x.srid}`} className="mono">{x.srid}</Link> · {x.why}, so it does not count
              </div>
            ))}
          </div>
        )}
        <button type="button" onClick={() => setShowQ(!showQ)} className="b3" style={{ marginTop: 14, fontSize: 14 }}>{showQ ? "Hide the query" : "Show the query"}</button>
        {showQ && p && <pre className="code">{p.queries.join("\n")}{"\n"}// params: id = &apos;{a.ntsb_num}&apos; → no_linked_warning = true</pre>}
        <p style={{ margin: "18px 0 0", fontSize: 13, color: "var(--sub)" }}>
          {LINKED_DEF} This covers the NTSB record as of the latest export; it makes no claim about the crash&apos;s causes beyond the NTSB&apos;s findings.
        </p>
      </section>
      {proposals.length > 0 && (
        <section style={{ maxWidth: 900, border: "1px dashed var(--ink)", padding: "18px 22px", display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
          <div>
            <div className="k">Unverified · not part of this answer</div>
            <div style={{ marginTop: 4 }}>
              The Analyst proposed {proposals.length === 1 ? "one possible connection" : `${proposals.length} possible connections`}, to{" "}
              {proposals.slice(0, 3).map((x, i) => <span key={x.id}>{i ? ", " : ""}<span className="mono">{x.id}</span></span>)}
              {proposals.length > 3 ? " and others" : ""}, that no person has checked.
            </div>
          </div>
          <Link href="/review" style={{ minHeight: 44, display: "inline-flex", alignItems: "center", fontWeight: 600 }}>Review {proposals.length === 1 ? "it" : "them"} →</Link>
        </section>
      )}
    </>
  );
}

function TextAnswer({ kicker, text, link }: { kicker: string; text: string; link: [string, string] }) {
  const [first, ...rest] = text.split("\n");
  return (
    <section className="sec" style={{ maxWidth: 900 }}>
      <div className="k">{kicker}</div>
      <h1 className="h1" style={{ fontSize: 34, lineHeight: 1.2 }}>{first}</h1>
      <div style={{ marginTop: 16, display: "flex", flexDirection: "column" }}>
        {rest.filter((l) => l.trim()).map((l, i) => <div key={i} className="row" style={{ padding: "10px 0", fontSize: 14 }}>{l.replace(/^- /, "")}</div>)}
      </div>
      <Link href={link[0]} className="b1" style={{ marginTop: 18 }}>{link[1]}</Link>
    </section>
  );
}
