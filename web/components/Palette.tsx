"use client";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { asked, date, mark } from "@/lib/format";

type Hit = { kind: string; id: string; label: string; note?: string | null; target?: string | null };
const GROUPS: [string, string][] = [["Recommendation", "Warnings"], ["Accident", "Accidents"], ["Report", "Reports"], ["Organization", "Organisations"]];

export const openPalette = () => window.dispatchEvent(new Event("unheeded-palette"));

function href(h: Hit): string | null {
  if (h.kind === "Recommendation") return `/rec/${h.id}`;
  if (h.kind === "Accident" || h.kind === "Report") return h.target ? `/accident/${h.target}` : null;
  return `/org/${encodeURIComponent(h.id)}`;
}

function note(h: Hit): { text: string; style: React.CSSProperties } {
  if (h.kind === "Recommendation") {
    const m = mark(h.note);
    return { text: m.text, style: m.signal ? { color: "var(--signal)", fontWeight: 600, fontSize: 13 } : { color: "var(--sub)", fontSize: 13 } };
  }
  const n = Number(h.note);
  return { text: h.note && n > 19000000 ? date(n) : "", style: { color: "var(--sub)", fontSize: 13 } };
}

export default function Palette() {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  const [sel, setSel] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const router = useRouter();

  useEffect(() => {
    const show = () => setOpen(true);
    const key = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setOpen((o) => !o); }
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("unheeded-palette", show);
    window.addEventListener("keydown", key);
    return () => { window.removeEventListener("unheeded-palette", show); window.removeEventListener("keydown", key); };
  }, []);
  useEffect(() => { if (open) setTimeout(() => input.current?.focus(), 0); }, [open]);
  useEffect(() => {
    if (q.trim().length < 2) { setHits([]); return; }
    const t = setTimeout(() => api<{ results: Hit[] }>(`/api/search?q=${encodeURIComponent(q.trim())}`).then((r) => { setHits(r.results); setSel(0); }).catch(() => setHits([])), 150);
    return () => clearTimeout(t);
  }, [q]);
  if (!open) return null;

  const flat = GROUPS.flatMap(([kind]) => hits.filter((h) => h.kind === kind)).filter((h) => href(h));
  const go = (h: Hit) => { const to = href(h); if (to) { setOpen(false); setQ(""); router.push(to); } };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(s + 1, flat.length - 1)); }
    if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
    if (e.key === "Enter" && flat[sel]) go(flat[sel]);
  };

  return (
    <div onClick={() => setOpen(false)} style={{ position: "fixed", inset: 0, zIndex: 50, background: "var(--scrim)", padding: "96px 24px", boxSizing: "border-box", overflowY: "auto" }}>
      <div role="dialog" aria-modal="true" aria-label="Search the record" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 720, margin: "0 auto", background: "var(--bg)", border: "1px solid var(--ink)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "0 24px", borderBottom: "1px solid var(--ink)" }}>
          <label htmlFor="palette-q" className="k" style={{ whiteSpace: "nowrap" }}>Search</label>
          <input id="palette-q" ref={input} type="text" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKey}
            placeholder="A-16-035, ANC25MA018, AIR-26-05, FAA, Nome" autoComplete="off"
            style={{ flex: 1, height: 64, border: 0, outlineOffset: -2, background: "transparent", color: "var(--ink)", fontFamily: "'Newsreader', Georgia, serif", fontSize: 22 }} />
          <button type="button" className="kbd" onClick={() => setOpen(false)} aria-label="Close search" style={{ background: "transparent", color: "var(--sub)", cursor: "pointer" }}>Esc</button>
        </div>
        {flat.length > 0 && GROUPS.map(([kind, name]) => {
          const items = flat.filter((h) => h.kind === kind);
          if (!items.length) return null;
          return (
            <div key={kind}>
              <div className="k" style={{ padding: "16px 24px 6px" }}>{name}</div>
              {items.map((h) => {
                const n = note(h);
                return (
                  <a key={`${h.kind}${h.id}`} href={href(h) ?? "#"} className={`res${flat[sel] === h ? " on" : ""}`}
                     onClick={(e) => { e.preventDefault(); go(h); }}>
                    <span className="mono" style={{ fontWeight: 500 }}>{h.id}</span>
                    <span>{h.kind === "Recommendation" ? asked(h.label, 70) : h.label}</span>
                    <span style={n.style}>{n.text}</span>
                  </a>
                );
              })}
            </div>
          );
        })}
        {q.trim().length >= 2 && flat.length === 0 && (
          <div style={{ padding: "28px 24px", color: "var(--sub)" }}>No record matches “{q}”. Try a recommendation number like A-16-035, an NTSB number, or an organisation.</div>
        )}
        <div style={{ display: "flex", flexWrap: "wrap", gap: 18, padding: "14px 24px", borderTop: "1px solid var(--rule)", fontSize: 12, color: "var(--sub)" }}>
          <span><span className="kbd">↑</span> <span className="kbd">↓</span> move</span><span><span className="kbd">Enter</span> open</span><span><span className="kbd">Esc</span> close</span>
        </div>
      </div>
    </div>
  );
}
