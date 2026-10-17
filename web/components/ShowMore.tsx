"use client";
import { useState } from "react";

/** First `n` children, then a toggle for the rest. */
export default function ShowMore({ items, n, label }: { items: React.ReactNode[]; n: number; label: string }) {
  const [all, setAll] = useState(false);
  return (
    <>
      {(all ? items : items.slice(0, n))}
      {items.length > n && (
        <div style={{ padding: "12px 0", fontSize: 13, color: "var(--sub)" }}>
          Showing {all ? items.length : n} of {items.length} {label} · <button type="button" className="b3" style={{ minHeight: 0, fontSize: 13 }} onClick={() => setAll(!all)}>{all ? "Show fewer" : "Show all"}</button>
        </div>
      )}
    </>
  );
}
