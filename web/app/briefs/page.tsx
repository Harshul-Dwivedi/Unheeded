"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { api, type Brief, type Investigation, type RunResult } from "@/lib/api";
import { dropApi, loadApi, peekApi } from "@/lib/cache";
import { LINKED_DEF, NOT_PREVENTION } from "@/lib/format";
import { patchInvestigateBrief } from "@/lib/session";
import { notifyCounts, useWorkspace, WORKSPACES } from "@/lib/workspace";

type Listed = { id: string; question: string; started: number; brief?: { id: string; status: string; thread?: string } | null };

export default function Page() {
  return <Suspense><Briefs /></Suspense>;
}

/** Brief text with [citations] as links; ids are never re-typed, only linked. */
function Cited({ text }: { text: string }) {
  const parts = text.split(/(\[[^\]]{1,40}\])/g);
  return (
    <>
      {parts.map((p, i) => {
        if (!/^\[.+\]$/.test(p)) return <span key={i}>{p}</span>;
        const id = p.slice(1, -1);
        const href = /^A-\d{2}-\d{3}$/.test(id) ? `/rec/${id}` : /^[A-Z]{3}\d{2}[A-Z]{2}\d{3}/.test(id) ? `/accident/${id}` : null;
        const style = { fontFamily: "'IBM Plex Mono', monospace", fontSize: 13 };
        return href ? <Link key={i} href={href} style={style}>{id}</Link> : <span key={i} style={{ ...style, color: "var(--sub)" }}>{id}</span>;
      })}
    </>
  );
}

function Briefs() {
  const params = useSearchParams();
  const ws = useWorkspace();
  const [list, setList] = useState<Listed[]>([]);
  const [inv, setInv] = useState<string | null>(params.get("inv"));
  const [d, setD] = useState<Investigation | null>(null);
  const [edit, setEdit] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [showQ, setShowQ] = useState(false);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!ws) return;
    const apply = (r: { investigations: Listed[] }) => {
      const withBrief = r.investigations.filter((i) => i.brief);
      setList(withBrief);
      setInv((cur) => cur ?? (withBrief.find((i) => ["awaiting_approval", "blocked"].includes(i.brief!.status)) ?? withBrief[0])?.id ?? null);
    };
    const cached = peekApi<{ investigations: Listed[] }>(`/api/workspaces/${ws}/investigations`);
    if (cached) apply(cached);
    loadApi<{ investigations: Listed[] }>(`/api/workspaces/${ws}/investigations`).then(apply).catch(() => {});
  }, [ws]);
  const load = useCallback(() => { if (ws && inv) api<Investigation>(`/api/workspaces/${ws}/investigations/${inv}`).then(setD).catch((e) => setMsg(e.message)); }, [ws, inv]);
  useEffect(load, [load]);

  const b: Brief | undefined = d?.briefs?.[0];
  const v = b?.verdict;
  const blockedLines = new Set((v?.blocked ?? []).map((x) => x.line.trim()));
  const gateStep = d?.steps.filter((s) => s.agent === "gate").slice(-1)[0];

  const decide = async (action: "approve" | "reject" | "edit" | "send_back", text?: string, noteText?: string) => {
    if (!ws || !b?.thread) return;
    setBusy(true); setMsg("");
    try {
      const s = await api<RunResult>(`/api/workspaces/${ws}/runs/${encodeURIComponent(b.thread)}/decision`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action, reviewer: ws ? WORKSPACES[ws] ?? ws : "reviewer", text, note: noteText }),
      });
      if (s.brief) patchInvestigateBrief(s.investigation, s.brief);
      dropApi(`/api/workspaces/${ws}/investigations`);
      setEdit(null); setNote(null); load(); notifyCounts();
    } catch (e) {
      setMsg((e as { status?: number }).status === 404 ? "This brief is no longer waiting for a decision. Reload to see its status." : (e as Error).message);
    }
    setBusy(false);
  };
  const removeBlocked = () => b && decide("edit", b.text.split("\n").filter((l) => !blockedLines.has(l.trim())).join("\n"));

  const banner = !b ? null
    : b.status === "approved" ? { tag: "APPROVED", text: `Approved${b.decided_by ? ` by ${b.decided_by}` : ""} · saved to the ${ws ? WORKSPACES[ws] ?? ws : ""} workspace`, style: { border: "2px solid var(--ink)", color: "var(--ink)" } }
    : b.status === "rejected" ? { tag: "REJECTED", text: "Rejected · nothing was published", style: { background: "var(--wash)", color: "var(--sub)" } }
    : b.status === "sent_back" ? { tag: "SENT BACK", text: "Returned to Clerk with your note · the new draft replaces this one", style: { background: "var(--wash)", color: "var(--sub)" } }
    : b.status === "blocked" ? { tag: "APPROVAL BLOCKED", text: `${v?.blocked.length ?? 0} of ${v?.claims_checked ?? 0} claims have no supporting path in the graph`, style: { background: "var(--inverse)", color: "var(--inverseInk)" } }
    : { tag: "HELD FOR APPROVAL", text: "Nothing leaves this workspace until you approve", style: { background: "var(--inverse)", color: "var(--inverseInk)" } };
  const open = b && ["awaiting_approval", "blocked"].includes(b.status);

  return (
    <main className="wrap main" style={{ gap: 48 }}>
      {!ws || (list.length === 0 && !d) ? (
        <section className="sec"><div className="k">Briefs</div><p>No briefs in this workspace yet. <Link href="/">Ask about a crash</Link> and the Clerk drafts one.</p></section>
      ) : null}
      {banner && (
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "8px 20px", minHeight: 56, padding: "0 24px", ...banner.style }}>
          <span className="k" style={{ color: "inherit" }}>{banner.tag}</span><span>{banner.text}</span>
        </div>
      )}
      {msg && <p className="sig" role="alert">{msg}</p>}
      {b && d && (
        <div className="cols">
          <article className="sec" style={{ flex: "999 1 620px", minWidth: 0, maxWidth: 780 }}>
            <div className="k">{b.status === "approved" ? "Approved brief" : "Draft brief"} · written by Clerk · {d.question}</div>
            {edit == null ? (
              <div className="serif" style={{ marginTop: 16, fontSize: 20, lineHeight: 1.7, color: "var(--ink2)", display: "flex", flexDirection: "column", gap: 14 }}>
                {b.text.split("\n").filter((l) => l.trim()).map((line, i) => blockedLines.has(line.trim()) ? (
                  <p key={i} style={{ margin: 0, padding: "14px 18px", border: "2px solid var(--ink)", background: "var(--wash)" }}>
                    <span className="k" style={{ display: "block", marginBottom: 6, color: "var(--ink)" }}>✕ Blocked claim</span><Cited text={line} />
                  </p>
                ) : <p key={i} style={{ margin: 0 }}><Cited text={line} /></p>)}
                <p style={{ margin: "8px 0 0", paddingTop: 14, borderTop: "1px solid var(--rule2)", fontFamily: "'Public Sans', sans-serif", fontSize: 14, lineHeight: 1.6, color: "var(--sub)" }}>
                  {LINKED_DEF} {NOT_PREVENTION}
                </p>
              </div>
            ) : (
              <textarea value={edit} onChange={(e) => setEdit(e.target.value)} aria-label="Edit the brief" rows={16}
                style={{ marginTop: 16, width: "100%", boxSizing: "border-box", padding: 14, font: "inherit", fontSize: 15, lineHeight: 1.6, border: "1px solid var(--ink)", borderRadius: 0, background: "var(--bg)", color: "var(--ink)" }} />
            )}
          </article>
          <aside style={{ flex: "1 1 340px", minWidth: 0, display: "flex", flexDirection: "column", gap: 40 }}>
            <section className="sec">
              <div className="k">{v?.passed ? "Citation check" : "Why it was blocked"}</div>
              <p style={{ margin: "6px 0 10px", fontSize: 13, color: "var(--sub)" }}>Run by plain code, not the model. Every claim must match a path in the graph.</p>
              <div className="ck"><span>Claims checked</span><span style={{ fontWeight: 600 }}>{v?.claims_checked ?? 0}</span></div>
              <div className="ck"><span>Ids cited</span><span style={{ fontWeight: 600 }}>{b.citations.length}</span></div>
              <div className="ck" style={{ borderBottom: "1px solid var(--rule2)" }}><span>Unsupported claims</span>
                <span style={{ fontWeight: 600, ...(v?.blocked.length ? { color: "var(--signal)" } : {}) }}>{v?.blocked.length ? `✕ ${v.blocked.length}` : "✓ 0"}</span></div>
              {v?.blocked.map((x, i) => (
                <div key={i} style={{ marginTop: 14, fontSize: 14 }}>
                  <div style={{ fontWeight: 600 }}>{x.check}: {x.reason}</div>
                  {x.check === "link" && (
                    <>
                      <div className="ck"><span>Repeated in the report</span><span style={{ fontWeight: 600 }}>{x.weaker_path?.startsWith("REITERATED") ? "Found, outside the window" : "✕ Not found"}</span></div>
                      <div className="ck"><span>Reclassified in the report</span><span style={{ fontWeight: 600 }}>{x.weaker_path?.startsWith("REITERATED") ? "—" : "✕ Not found"}</span></div>
                      <div className="ck" style={{ borderBottom: "1px solid var(--rule2)" }}><span>Mentioned in the report</span><span>{x.weaker_path === "MENTIONED_IN" ? "Found, not enough" : "✕ Not found"}</span></div>
                    </>
                  )}
                  {x.path_searched && <div className="mono" style={{ marginTop: 6, fontSize: 12, color: "var(--sub)" }}>searched: {x.path_searched}</div>}
                </div>
              ))}
              {!!v?.warnings.length && <p style={{ fontSize: 13, color: "var(--sub)" }}>{v.warnings.length} style warning{v.warnings.length > 1 ? "s" : ""}: {v.warnings[0].reason}</p>}
              {gateStep?.cypher && (
                <>
                  <button type="button" onClick={() => setShowQ(!showQ)} className="b3" style={{ marginTop: 8, fontSize: 14 }}>{showQ ? "Hide the check queries" : "Show the check queries"}</button>
                  {showQ && <pre className="code" style={{ fontSize: 12 }}>{String(gateStep.cypher).split("\n;\n").join("\n\n")}</pre>}
                </>
              )}
            </section>
            {open && (
              <section className="sec" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <div className="k">{v?.passed ? "Your decision" : "Resolve before approval"}</div>
                {note != null ? (
                  <>
                    <label className="k" htmlFor="sendback-note">Note for the Clerk</label>
                    <textarea id="sendback-note" value={note} onChange={(e) => setNote(e.target.value)} rows={4} maxLength={1000}
                      placeholder="What should change? e.g. leave out the unverified proposals"
                      style={{ padding: 10, font: "inherit", fontSize: 14, border: "1px solid var(--ink)", borderRadius: 0, background: "var(--bg)", color: "var(--ink)" }} />
                    <button type="button" className="b1" disabled={busy || !note.trim()} onClick={() => decide("send_back", undefined, note)} style={{ justifyContent: "center" }}>Send back to Clerk</button>
                    <button type="button" className="b3" onClick={() => setNote(null)}>Cancel</button>
                  </>
                ) : edit != null ? (
                  <>
                    <button type="button" className="b1" disabled={busy} onClick={() => decide("edit", edit)} style={{ justifyContent: "center" }}>Check the edited brief</button>
                    <button type="button" className="b3" onClick={() => setEdit(null)}>Cancel editing</button>
                  </>
                ) : (
                  <>
                    {v?.passed
                      ? <button type="button" className="b1" disabled={busy} onClick={() => decide("approve")} style={{ justifyContent: "center", minHeight: 48, fontSize: 16 }}>Approve and publish to workspace</button>
                      : <button type="button" className="b1" disabled={busy} onClick={removeBlocked} style={{ justifyContent: "center" }}>Remove the blocked claim{(v?.blocked.length ?? 0) > 1 ? "s" : ""}</button>}
                    <button type="button" className="b2" disabled={busy} onClick={() => setEdit(b.text)} style={{ justifyContent: "center" }}>Edit, then re-check</button>
                    <button type="button" className="b2" disabled={busy} onClick={() => setNote("")} style={{ justifyContent: "center" }}>Send back to Clerk</button>
                    <button type="button" className="b3" disabled={busy} onClick={() => decide("reject")}>Reject</button>
                  </>
                )}
              </section>
            )}
          </aside>
        </div>
      )}
      {list.length > 1 && (
        <section className="sec">
          <div className="k" style={{ marginBottom: 6 }}>Other briefs in this workspace</div>
          {list.filter((i) => i.id !== inv).slice(0, 12).map((i) => (
            <button key={i.id} type="button" className="row" onClick={() => setInv(i.id)}
              style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) 170px", gap: 12, width: "100%", padding: "11px 0", border: 0, borderTop: "1px solid var(--rule2)", background: "transparent", color: "var(--ink)", font: "inherit", textAlign: "left", cursor: "pointer" }}>
              <span style={{ textDecoration: "underline", textUnderlineOffset: 3 }}>{i.question}</span>
              <span style={{ color: "var(--sub)", fontSize: 14 }}>{i.brief?.status.replace("_", " ")}</span>
            </button>
          ))}
        </section>
      )}
    </main>
  );
}
