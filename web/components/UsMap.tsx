"use client";
import { useEffect, useState } from "react";
import { pin } from "@/lib/albers";

/** Docket map (Albers USA, Alaska and Hawaii insets) with red pins sized 10 + sqrt(killed) * 3.2 px. */
export default function UsMap({ points, alt }: { points: { lat?: number; lon?: number; label?: string; fatal?: number; href?: string }[]; alt: string }) {
  const [dark, setDark] = useState(false);
  useEffect(() => {
    const read = () => setDark(document.documentElement.classList.contains("t-dark"));
    read();
    const obs = new MutationObserver(read);
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => obs.disconnect();
  }, []);
  return (
    <div style={{ position: "relative", width: "100%", aspectRatio: "975 / 610" }}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={dark ? "/docket-dark.svg" : "/docket-light.svg"} alt={alt} style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }} />
      {points.map((p, i) => {
        const at = pin(p.lat, p.lon);
        if (!at) return null;
        const size = 10 + Math.sqrt(p.fatal ?? 0) * 3.2;
        const dot = <div title={p.label} style={{ position: "absolute", left: `${at.left}%`, top: `${at.top}%`, width: size, height: size, transform: "translate(-50%, -50%)", background: "var(--signal)", border: "2px solid var(--bg)", borderRadius: "50%" }} />;
        return (
          <div key={i}>
            {p.href ? <a href={p.href} aria-label={p.label}>{dot}</a> : dot}
            {points.length === 1 && p.label && <div style={{ position: "absolute", left: `calc(${at.left}% + ${size / 2 + 6}px)`, top: `calc(${at.top}% - 9px)`, fontSize: 12, fontWeight: 600 }}>{p.label}</div>}
          </div>
        );
      })}
    </div>
  );
}
