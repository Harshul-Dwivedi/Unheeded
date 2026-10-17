import Link from "next/link";
import { api } from "@/lib/api";

type Card = {
  eval1?: { pass: boolean; recall: string; false_positives: number; median_ms: number };
  eval2?: { pass: boolean; average: Record<string, { "hit@10": number; mrr: number; checked_per_crash: number }> };
  eval3?: { pass: boolean; errors: number; claims: number }; eval4?: { pass: boolean; p95_ms: number };
  concurrency?: { claims_ok: boolean; writes_per_s: number }; gold: { crashes: number; links: number };
};

const Li = ({ mark, children, last }: { mark: React.ReactNode; children: React.ReactNode; last?: boolean }) => (
  <div className="li" style={last ? { borderBottom: "1px solid var(--rule2)" } : undefined}><span className="mono" style={{ color: "var(--sub)" }}>{mark}</span><span>{children}</span></div>
);

export default async function Method() {
  const c = await api<Card>("/api/eval").catch(() => null);
  const e2 = c?.eval2?.average;
  return (
    <main className="wrap" style={{ paddingTop: 48 }}>
      <div style={{ maxWidth: 860 }}>
        <div className="k">Method and sources</div>
        <h1 className="h1">How Unheeded works, and what it does not claim</h1>
        <p className="p" style={{ fontStyle: "italic" }}>“Unheeded” is a question, not a verdict. We show which NTSB warnings were still open the day a plane crashed, and how the NTSB itself rated the response then. Often it was rated acceptable.</p>
        <p className="p" style={{ marginTop: 16, fontSize: 18, color: "var(--sub)" }}>
          Every number in this product comes from public NTSB records. This page explains where they come from, what our terms mean, and where the record has gaps.
        </p>
      </div>
      <div className="cols" style={{ marginTop: 56 }}>
        <nav className="toc" aria-label="On this page">
          {[["data", "The data"], ["linked", "What “linked” means"], ["not", "What it does not mean"], ["marks", "Status marks"], ["agents", "What the agents may do"], ["evals", "How we test it"], ["limits", "Known limits"]]
            .map(([id, t]) => <a key={id} href={`#${id}`}>{t}</a>)}
        </nav>
        <div className="body">
          <section id="data" className="sec">
            <div className="k">01</div><h2 className="h2">The data</h2>
            <p className="p">Three public exports from the NTSB’s CAROL database: every US aviation safety recommendation (5,964 aviation records from the 1960s to 2026, with their full letter threads), the 1,133 investigations that produced recommendations, and 9,530 aviation investigations from 2021 to 2026. All are US federal records in the public domain. Nothing is private, paywalled or behind a login.</p>
            <p className="p">They are loaded into one FalkorDB graph: recommendations, accidents, reports, letters and organisations, joined by the NTSB’s own references, such as “repeated in report AIR-26-05”.</p>
          </section>
          <section id="linked" className="sec">
            <div className="k">02</div><h2 className="h2">What “linked warning” means</h2>
            <p className="p">We use one definition everywhere. A recommendation counts as a linked warning for a crash only when all three are true:</p>
            <div style={{ display: "flex", flexDirection: "column", marginTop: 8 }}>
              <Li mark="1">The NTSB repeated or reclassified it in that crash’s own report. A passing mention does not count.</Li>
              <Li mark="2">It was issued before the crash. The issue date is the earliest of the origin report or Board date, the first letter, the closing date and 31 December of the year in its number (A-<b>25</b>-001 was issued in 2025).</Li>
              <Li mark="3" last>It was still open on the day of the crash.</Li>
            </div>
            <p className="p" style={{ marginTop: 14, fontSize: 14, color: "var(--sub)" }}>On the current export: {c?.gold.crashes ?? 16} crashes, {c?.gold.links ?? 54} links.</p>
          </section>
          <section id="not" className="sec">
            <div className="k">03</div><h2 className="h2">What it does not mean</h2>
            <p className="p">A linked warning shows that the NTSB connected an earlier recommendation to a later crash. It does not establish that acting on the recommendation would have prevented that crash. We report the NTSB’s own ratings, such as “unacceptable response”, and add no judgement of our own.</p>
          </section>
          <section id="marks" className="sec">
            <div className="k">04</div><h2 className="h2">Status marks</h2>
            <div style={{ display: "flex", flexDirection: "column" }}>
              <Li mark={<span className="sig">■</span>}><b>Open, rated unacceptable.</b> The NTSB judged the recipient’s response unacceptable.</Li>
              <Li mark="□"><b>Open.</b> Awaiting a response, or the response was rated acceptable or an acceptable alternative.</Li>
              <Li mark="—" last><b>Closed.</b> With the NTSB’s closing rating shown in words.</Li>
            </div>
            <p className="p" style={{ marginTop: 14, fontSize: 14, color: "var(--sub)" }}>
              Status on a past date comes from the NTSB’s classification letters (“we classify … Open—Unacceptable Response”), read by plain code; on recommendations with such a letter, the last classification matches the current status 89% of the time. A solid border marks an NTSB record; a dashed border marks an agent proposal no person has checked.
            </p>
          </section>
          <section id="agents" className="sec">
            <div className="k">05</div><h2 className="h2">What the agents may do</h2>
            <div style={{ display: "flex", flexDirection: "column" }}>
              <Li mark="✓">Run read-only graph queries through MCP tools, each with a time limit and a row limit. FalkorDB itself refuses them any write.</Li>
              <Li mark="✓">Choose extra evidence to fetch, within a fixed tool list and budget, and write the brief’s wording.</Li>
              <Li mark="✓">Propose connections the NTSB did not make, clearly marked as unverified.</Li>
              <Li mark="✕">State a date, status or id that a graph query did not return. An approval gate (plain code, not a model) checks every claim against the graph.</Li>
              <Li mark="✕">Publish anything without a person’s approval, or cite an unverified proposal in a brief.</Li>
              <Li mark="✕" last>Name or describe individuals. Names in quoted official letters are removed before display.</Li>
            </div>
          </section>
          <section id="evals" className="sec">
            <div className="k">06</div><h2 className="h2">How we test it</h2>
            <div style={{ display: "flex", flexDirection: "column" }}>
              <Li mark="E1">Linked-warning recovery: {c?.eval1 ? `${c.eval1.recall} gold links, ${c.eval1.false_positives} false positives on 50 crashes with none, ${c.eval1.median_ms} ms median` : "not run"}.</Li>
              <Li mark="E2">Learning from review (hidden half): gold link in the top 10 {e2 ? `${e2.cold["hit@10"]}% → ${e2.warm["hit@10"]}%, candidates checked ${e2.cold.checked_per_crash} → ${e2.warm.checked_per_crash}` : "not run"}. The gain comes from confirmed links reused as search seeds. Weights fitted to review decisions (logistic regression) barely move the ranking: on this data the default mix of signals is already about the best linear one (separation 0.737 vs 0.738), and text similarity alone barely separates true links (0.53).</Li>
              <Li mark="E3">Approval gate: {c?.eval3 ? `${c.eval3.errors} errors on ${c.eval3.claims} hand-written claims` : "not run"} (20 true, 20 with planted mistakes).</Li>
              <Li mark="E4" last>Graph work and speed: {c?.eval4 ? `every tool call p95 ${c.eval4.p95_ms} ms` : "not run"}{c?.concurrency ? `; concurrent agents never claim the same task (${c.concurrency.writes_per_s.toLocaleString("en-US")} memory writes/s)` : ""}.</Li>
            </div>
          </section>
          <section id="limits" className="sec">
            <div className="k">07</div><h2 className="h2">Known limits</h2>
            <div style={{ display: "flex", flexDirection: "column" }}>
              <Li mark="·">Issue dates: the first letter sets 3,322 of them, the origin report 1,496, and for 1,146 only the year is known (we use 31 December, the latest possible day).</Li>
              <Li mark="·">133 recommendations come from safety studies or special investigations rather than single accidents.</Li>
              <Li mark="·">Probable-cause text is missing for most accidents before the 1990s, and coded findings are dense only from about 2008, so proposals for older warnings lean on text similarity.</Li>
              <Li mark="·">The export repeats letters (up to seven times); duplicates are removed before counting.</Li>
              <Li mark="·">The gate understands the brief formats the Clerk is told to use. A claim in free-form prose may go unchecked, which is why a person approves every brief. Names are detected by pattern, not by a full name recogniser.</Li>
              <Li mark="·" last>Operators and aircraft types are not in the exports, so the product never states them.</Li>
            </div>
            <p className="p" style={{ marginTop: 14, fontSize: 14 }}><Link href="/">Back to Investigate</Link></p>
          </section>
        </div>
      </div>
    </main>
  );
}
