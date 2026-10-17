// Display rules from docs/design-system.md (Docket) and docs/method-and-ethics.md (wording): NTSB terms, glyph + words, never colour alone.

/** The one definition, worded the same everywhere it is shown (docs/method-and-ethics.md). */
export const LINKED_DEF = "Linked: the NTSB repeated or reclassified the warning in this crash's own report (a mention doesn't count), it was issued before the crash, and it was still open that day.";
export const NOT_PREVENTION = "A linked warning does not establish that acting on it would have prevented the crash.";

const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTH = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

export function date(d?: number | null, long = false): string {
  if (!d) return "unknown date";
  const y = Math.floor(d / 10000), m = Math.floor(d / 100) % 100, day = d % 100;
  return `${day} ${(long ? MONTH : MON)[m - 1]} ${y}`;
}

export function year(d?: number | null): string {
  return d ? String(Math.floor(d / 10000)) : "";
}

export function openFor(days?: number | null): string {
  if (days == null) return "";
  if (days < 365) return "< 1 yr";
  return `~${Math.round(days / 365.25)} yrs`;
}

export function times(n?: number | null): string {
  if (!n) return "never";
  return n === 1 ? "once" : `${n} times`;
}

export type Mark = { text: string; signal: boolean; muted: boolean };

const MARKS: Record<string, [string, boolean]> = {
  // code: [Docket shorthand (docs/design-system.md status marks), signal]
  OUA: ["■ Open — unacceptable", true], OAA: ["□ Open — acceptable", false], OAAR: ["□ Open — acceptable alternate", false],
  OAR: ["□ Open — awaiting response", false], ORR: ["□ Open — initial response received", false],
  CAA: ["— Closed, acceptable", false], CAAA: ["— Closed, acceptable alternate", false], CAAS: ["— Closed, acceptable, superseded", false],
  CUA: ["— Closed, unacceptable", true], CUAN: ["— Closed, unacceptable (no response)", true], CUAS: ["— Closed, unacceptable, superseded", true],
  CR: ["— Closed, reconsidered", false], CNLA: ["— Closed, no longer applicable", false], CEX: ["— Closed, exceeds recommended action", false],
  CS: ["— Closed, superseded", false],
};

/** NTSB status code -> Docket status mark (glyph + words). Signal red only for unacceptable. Full NTSB label goes in `title`. */
export function mark(code?: string | null, label?: string | null): Mark {
  const m = MARKS[code ?? ""];
  if (!m) return { text: label ?? "unknown", signal: false, muted: true };
  return { text: m[0], signal: m[1], muted: !m[1] && (code ?? "").startsWith("C") };
}

export function markStyle(m: Mark): React.CSSProperties {
  return m.signal ? { color: "var(--signal)", fontWeight: 600 } : m.muted ? { color: "var(--sub)" } : { color: "var(--ink)" };
}

/** "TO THE FEDERAL AVIATION ADMINISTRATION: Require ..." -> a short, deterministic excerpt of the official text. */
export function asked(text?: string | null, max = 110): string {
  if (!text) return "";
  const body = text.replace(/^TO\s+[^:]{1,240}:\s*/i, "").replace(/\s+/g, " ").trim();
  if (body.length <= max) return body;
  const cut = body.slice(0, max);
  const at = Math.max(cut.lastIndexOf(", "), cut.lastIndexOf("; "), cut.lastIndexOf(" "));
  return cut.slice(0, at > 60 ? at : max).replace(/[,;]$/, "") + "…";
}

export function addressee(text?: string | null): string {
  const m = /^TO\s+(?:THE\s+)?([^:]{1,240}):/i.exec(text ?? "");
  return m ? m[1].replace(/\b\w/g, (c) => c.toUpperCase()).replace(/\B\w+/g, (w) => w.toLowerCase()) : "";
}

export const place = (city?: string | null, state?: string | null) => [city, state].filter(Boolean).join(", ");

export const words = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve"];
export const count = (n: number) => (n < words.length ? words[n] : String(n));
export const lowerCount = (n: number) => count(n).toLowerCase();
