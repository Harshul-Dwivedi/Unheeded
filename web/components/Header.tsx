"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { prefetchDashboard } from "@/lib/cache";
import { setWorkspace, useWorkspace, WORKSPACES } from "@/lib/workspace";
import { openPalette } from "./Palette";

const NAV = [
  ["/", "Investigate"], ["/insights", "Insights"], ["/review", "Review"], ["/agents", "Agents"], ["/briefs", "Briefs"], ["/watch", "Watch"],
] as const;

export default function Header() {
  const path = usePathname();
  const router = useRouter();
  const ws = useWorkspace();
  const [dark, setDark] = useState(false);
  const [counts, setCounts] = useState<{ review?: number; briefs?: number }>({});

  useEffect(() => setDark(document.documentElement.classList.contains("t-dark")), []);
  useEffect(() => { if (ws) prefetchDashboard(ws, false, router); }, [ws, router]);
  useEffect(() => {
    if (!ws) return;
    const load = () => {
      api<{ proposals: unknown[] }>(`/api/workspaces/${ws}/review`).then((r) => setCounts((c) => ({ ...c, review: r.proposals.length }))).catch(() => {});
      api<{ investigations: { brief?: { status: string } | null }[] }>(`/api/workspaces/${ws}/investigations`)
        .then((r) => setCounts((c) => ({ ...c, briefs: r.investigations.filter((i) => ["awaiting_approval", "blocked"].includes(i.brief?.status ?? "")).length })))
        .catch(() => {});
    };
    load();
    window.addEventListener("unheeded-counts", load);
    return () => window.removeEventListener("unheeded-counts", load);
  }, [ws, path]);

  const toggle = () => {
    const next = dark ? "light" : "dark";
    document.documentElement.classList.remove("t-light", "t-dark");
    document.documentElement.classList.add(`t-${next}`);
    try { window.localStorage.setItem("unheeded-theme", next); } catch {}
    setDark(!dark);
  };
  const on = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));
  const label = (href: string, name: string) =>
    href === "/review" && counts.review ? `${name} (${counts.review})` : href === "/briefs" && counts.briefs ? `${name} (${counts.briefs})` : name;

  return (
    <header className="hd">
      <div className="wrap hdin">
        <Link href="/" className="logo"><span className="sq" aria-hidden="true" />Unheeded</Link>
        <nav className="nav" aria-label="Main">
          {NAV.map(([href, name]) => (
            <Link key={href} href={href} className={`nl${on(href) ? " on" : ""}`} aria-current={on(href) ? "page" : undefined}>{label(href, name)}</Link>
          ))}
        </nav>
        <div className="tools">
          <button type="button" className="srch" onClick={openPalette} style={{ background: "transparent", font: "inherit", cursor: "pointer" }}>
            Search IDs <span className="kbd">⌘K</span>
          </button>
          <select className="sel" aria-label="Workspace" value={ws ?? "desk"} onChange={(e) => setWorkspace(e.target.value)}>
            {Object.entries(WORKSPACES).map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </select>
          <button type="button" onClick={toggle} className="tbtn">{dark ? "Light mode" : "Dark mode"}</button>
        </div>
      </div>
    </header>
  );
}
