import { mark, markStyle } from "@/lib/format";

/** NTSB status as glyph + words (never colour alone); the full NTSB label is the tooltip. */
export default function Status({ code, label, style }: { code?: string | null; label?: string | null; style?: React.CSSProperties }) {
  const m = mark(code, label);
  return <span title={label ?? undefined} style={{ ...markStyle(m), ...style }}>{m.text}</span>;
}
