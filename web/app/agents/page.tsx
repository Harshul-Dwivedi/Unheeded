"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { type Investigation, type Step } from "@/lib/api";
import { loadApi, peekApi } from "@/lib/cache";
import { useWorkspace } from "@/lib/workspace";

export default function Page() {
  return <Suspense><Board /></Suspense>;
}

type Look = "done" | "active" | "waiting" | "human";
const LOOK: Record<Look, React.CSSProperties> = {
  done: { background: "var(--wash)", border: "1px solid var(--rule)", color: "var(--sub)" },
  active: { background: "var(--bg)", border: "2px solid var(--ink)", color: "var(--ink)" },
  waiting: { background: "var(--bg)", border: "1px dashed var(--muted)", color: "var(--sub)" },
  human: { background: "var(--inverse)", border: "1px solid var(--inverse)", color: "var(--inverseInk)" },
};
// columns from the prototype (px); one column per agent, its task underneath
const COLS = [
  { agent: "orchestrator", name: "Orchestrator", x: 32, task: "plan" },
  { agent: "scout", name: "Scout", x: 284, task: "resolve" },
  { agent: "analyst", name: "Analyst", x: 536, task: "analyse" },
  { agent: "clerk", name: "Clerk", x: 788, task: "draft_brief" },
  { agent: "gate", name: "Approval gate", x: 1076, task: "approve_brief" },
];

function Arrow({ x, y, w = 0, h = 0, dashed, back }: { x: number; y: number; w?: number; h?: number; dashed?: boolean; back?: boolean }) {
  const horiz = w > 0;
  const [W, H] = horiz ? [w, 8] : [8, h];
  const d = horiz ? (back ? `M ${w} 4 L 4 4` : `M 0 4 L ${w - 4} 4`) : `M 4 0 L 4 ${h - 4}`;
  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} aria-hidden="true" style={{ position: "absolute", left: x, top: y, overflow: "visible", fill: "none", stroke: "var(--muted)", strokeWidth: 2, ...(dashed ? { strokeDasharray: "6 6" } : {}) }}>
      <defs><marker id={`h${x}${y}`} orient="auto" markerWidth="5" markerHeight="5" refX="2" refY="2"><path d="M0 0 L4 2 L0 4 Z" fill="var(--muted)" stroke="none" /></marker></defs>
      <path d={d} markerEnd={`url(#h${x}${y})`} />
    </svg>
  );
}

const caption = (s: Step) => {
  const name = COLS.find((c) => c.agent === s.agent)?.name ?? s.agent;
  const what = s.tool === "plan" ? "reads the question and plans" : s.tool === "check" ? "checks every claim in the brief against the graph" : `calls ${s.tool}`;
  const rows = s.result_count == null ? "" : s.tool === "check" ? ` · ${s.result_count ? `${s.result_count} claim${s.result_count === 1 ? "" : "s"} blocked` : "all claims supported"}`
    : ` · ${s.result_count} result${s.result_count === 1 ? "" : "s"}`;
  const ms = s.ms != null ? ` · ${s.ms} ms` : "";
  return `${name} ${what}${rows}${ms}`;
};

function Board() {
  const params = useSearchParams();
  const ws = useWorkspace();
  const [list, setList] = useState<{ id: string; question: string }[]>([]);
  const [inv, setInv] = useState<string | null>(params.get("inv"));
  const [d, setD] = useState<Investigation | null>(null);
  const [at, setAt] = useState(1);
  const [checked, setChecked] = useState<{ cold: number; warm: number } | null>(null);
  const [showQ, setShowQ] = useState(false);

  useEffect(() => {
    if (!ws) return;
    const apply = (r: { investigations: { id: string; question: string }[] }) => {
      setList(r.investigations); setInv((cur) => cur ?? r.investigations[0]?.id ?? null);
    };
    const cached = peekApi<{ investigations: { id: string; question: string }[] }>(`/api/workspaces/${ws}/investigations`);
    if (cached) apply(cached);
    loadApi<{ investigations: { id: string; question: string }[] }>(`/api/workspaces/${ws}/investigations`).then(apply).catch(() => {});
  }, [ws]);
  useEffect(() => {
    if (ws && inv) {
      const path = `/api/workspaces/${ws}/investigations/${inv}`;
      const cached = peekApi<Investigation>(path);
      if (cached) { setD(cached); setAt(cached.steps.length); }
      loadApi<Investigation>(path).then((x) => { setD(x); setAt(x.steps.length); }).catch(() => {});
    }
  }, [ws, inv]);
  useEffect(() => {
    loadApi<{ eval2?: { average: Record<string, { checked_per_crash: number }> } }>("/api/eval")
      .then((e) => e.eval2 && setChecked({ cold: e.eval2.average.cold.checked_per_crash, warm: e.eval2.average.warm.checked_per_crash })).catch(() => {});
  }, []);

  if (!ws || (!d && list.length === 0)) {
    return <main className="wrap main"><section className="sec"><div className="k">Agent board</div><p>No investigations in this workspace yet. <Link href="/">Ask a question</Link> to watch the agents work.</p></section></main>;
  }
  const steps = d?.steps ?? [];
  const s = Math.min(Math.max(at, 1), steps.length || 1);
  const brief = d?.briefs?.[0];
  const status = (agent: string): Look => {
    const mine = steps.map((x, i) => (x.agent === agent ? i + 1 : 0)).filter(Boolean);
    if (agent === "gate" && s === steps.length && brief && ["awaiting_approval", "blocked"].includes(brief.status)) return "human";
    if (mine.includes(s)) return "active";
    if (mine.length && mine.every((i) => i < s)) return "done";
    return "waiting";
  };
  const taskOf = (kind: string) => d?.tasks.find((t) => t.kind === kind);
  const taskLabel = (c: (typeof COLS)[number], look: Look) => {
    if (c.agent === "gate") return look === "human" ? (brief?.status === "blocked" ? "Blocked · needs you" : "Waiting for you") : look === "done" ? (brief?.status === "approved" ? "Approved" : brief?.status ?? "Done") : look === "active" ? "Checking" : "Waiting";
    if (c.agent === "orchestrator") return look === "waiting" ? "Waiting" : "Done · plan made";
    const t = taskOf(c.task);
    const calls = steps.filter((x, i) => x.agent === c.agent && i < s).length;
    if (look === "active") return `Claimed by ${t?.claimed_by ?? c.agent} · lease 60 s`;
    if (look === "done") return `Done · ${calls} tool call${calls === 1 ? "" : "s"}${t && (t.attempts ?? 0) > 1 ? ` · ${t.attempts} attempts` : ""}`;
    return "Waiting";
  };
  const cur = steps[s - 1];
  return (
    <main className="wrap main">
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "flex-end", gap: 16 }}>
        <div style={{ maxWidth: 760 }}>
          <div className="k">Agent board · replay of a real run · {d?.question}</div>
          <h1 className="h1">Who is working on what</h1>
          <p style={{ margin: "10px 0 0", fontSize: 16, color: "var(--sub)" }}>Agents hand work to each other through task nodes in the workspace graph, never through chat. Every step below is a Step node.</p>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
          <button type="button" className="b2" onClick={() => setAt(Math.max(1, s - 1))} disabled={s <= 1}>Back</button>
          <button type="button" className="b1" onClick={() => setAt(Math.min(steps.length, s + 1))} disabled={s >= steps.length}>Next step</button>
          <button type="button" className="b3" onClick={() => setAt(1)}>Replay from start</button>
        </div>
      </div>
      <section className="sec">
        <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", gap: 10, marginBottom: 6 }}>
          <div className="k">Step {s} of {steps.length}</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 18, fontSize: 12, color: "var(--sub)" }}>
            {(["done", "active", "waiting", "human"] as Look[]).map((l) => (
              <span key={l} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                <span style={{ width: 12, height: 12, boxSizing: "border-box", ...LOOK[l] }} />{{ done: "Done", active: "Claimed", waiting: "Waiting", human: "Needs you" }[l]}
              </span>
            ))}
          </div>
        </div>
        <p className="serif" style={{ margin: "0 0 16px", fontSize: 22 }} aria-live="polite">{cur ? caption(cur) : "…"}</p>
        <div style={{ overflowX: "auto" }}>
          <div style={{ position: "relative", width: 1292, height: 270 }}>
            {[216, 468, 720].map((x) => <Arrow key={x} x={x} y={72} w={67} />)}
            <Arrow x={972} y={60} w={103} />
            <Arrow x={973} y={84} w={103} dashed back />
            <div style={{ position: "absolute", left: 986, top: 100, width: 76, textAlign: "center", fontSize: 13, lineHeight: "16px", color: "var(--sub)" }}>send back</div>
            {COLS.map((c) => <Arrow key={`v${c.x}`} x={c.x + 88} y={112} h={63} />)}
            {COLS.map((c) => {
              const look = status(c.agent);
              return (
                <div key={c.agent}>
                  <div style={{ position: "absolute", left: c.x, top: 40, width: 184, height: 72, boxSizing: "border-box", padding: "8px 14px", display: "flex", alignItems: "center", fontSize: 14, lineHeight: "20px", ...LOOK[look] }}>
                    <span><b>{c.name}</b><br />{look === "active" ? "Working" : look === "human" ? "Needs your decision" : look === "done" ? "Done" : "Idle"}</span>
                  </div>
                  <div style={{ position: "absolute", left: c.x, top: 176, width: 184, height: 64, boxSizing: "border-box", padding: "8px 14px", display: "flex", alignItems: "center", fontSize: 13, lineHeight: "20px", ...LOOK[look] }}>
                    <span><b className="mono" style={{ fontWeight: 500 }}>{c.task}</b><br />{taskLabel(c, look)}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </section>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 40, alignItems: "flex-start" }}>
        <section className="sec" style={{ flex: "999 1 560px", minWidth: 0 }}>
          <div className="k" style={{ marginBottom: 8 }}>Handoff log · Step nodes, with timings</div>
          <div style={{ display: "flex", flexDirection: "column", fontSize: 14 }}>
            {steps.slice(0, s).map((x, i) => (
              <button key={x.n} type="button" onClick={() => setAt(i + 1)}
                style={{ display: "grid", gridTemplateColumns: "70px minmax(0, 1fr) 80px", gap: 14, padding: "10px 0", border: 0, borderTop: "1px solid var(--rule2)", background: "transparent", font: "inherit", textAlign: "left", cursor: "pointer", color: i + 1 === s ? "var(--ink)" : "var(--sub)", fontWeight: i + 1 === s ? 600 : 400 }}>
                <span className="mono" style={{ fontSize: 13, color: "var(--muted)" }}>Step {i + 1}</span><span>{caption(x)}</span>
                <span className="mono" style={{ fontSize: 12, textAlign: "right", color: "var(--muted)" }}>{x.ms != null ? `${x.ms} ms` : ""}</span>
              </button>
            ))}
          </div>
          {cur?.cypher && (
            <>
              <button type="button" className="b3" style={{ marginTop: 8, fontSize: 14 }} onClick={() => setShowQ(!showQ)}>{showQ ? "Hide" : "Show"} the Cypher for step {s}</button>
              {showQ && <pre className="code" style={{ fontSize: 12 }}>{String(cur.cypher).split("\n;\n").join("\n\n")}</pre>}
            </>
          )}
        </section>
        <section className="sec" style={{ flex: "1 1 380px", minWidth: 0 }}>
          <div className="k">Memory at work</div>
          <p style={{ margin: "6px 0 14px", fontSize: 13, color: "var(--sub)" }}>Eval 2, hidden half: candidates a reviewer checks before the first true link, before and after the workspace learned from review</p>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", rowGap: 14 }}>
            <div className="fact"><div style={{ fontSize: 12, color: "var(--sub)" }}>Candidates checked · first run</div><div className="serif" style={{ fontSize: 26 }}>{checked?.cold ?? "—"}</div></div>
            <div className="fact"><div style={{ fontSize: 12, color: "var(--sub)" }}>Candidates checked · after learning</div><div className="serif" style={{ fontSize: 26 }}>{checked?.warm ?? "—"}</div></div>
          </div>
          {list.length > 1 && (
            <div style={{ marginTop: 28 }}>
              <div className="k" style={{ marginBottom: 6 }}>Other runs</div>
              {list.filter((x) => x.id !== inv).slice(0, 8).map((x) => (
                <button key={x.id} type="button" className="b3" style={{ display: "block", minHeight: 36, fontSize: 14, textAlign: "left" }} onClick={() => setInv(x.id)}>{x.question}</button>
              ))}
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
