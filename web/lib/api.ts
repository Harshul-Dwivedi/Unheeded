// Typed access to the FastAPI backend. Server components call it directly; the browser goes through /api (next.config rewrites).

export const SERVER_API = process.env.API_URL ?? "http://127.0.0.1:8000";

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const base = typeof window === "undefined" ? SERVER_API : "";
  const res = await fetch(`${base}${path}`, { cache: "no-store", ...init });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(res.status, (body as { detail?: string }).detail ?? res.statusText);
  }
  return res.json() as Promise<T>;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export type Accident = {
  ntsb_num: string; event_date: number; city?: string; state?: string; country?: string; lat?: number; lon?: number;
  fatal?: number; injury_level?: string; probable_cause?: string; in_pool?: boolean; partial?: boolean; family_id?: number;
};
export type LinkedWarning = {
  srid: string; text: string; issued: number; times_reiterated: number; code_today: string; status_today: string;
  code_then?: string; status_then?: string; status_since?: number; origin_ntsb?: string; origin_place?: string;
  origin_date?: number; owners: string[]; open_days?: number;
};
export type AccidentPage = {
  accident: Accident; reports: string[]; linked: LinkedWarning[]; mentions: { srid: string; report: string }[];
  rated_unacceptable_today: number; rated_unacceptable_then: number; similar: { ntsb_num: string; city: string; event_date: number; fatal: number; similarity: number; same_family: boolean }[];
  absence: null | { found: boolean; no_linked_warning: boolean; reports: string[]; has_report: boolean;
                    repeated_outside_definition: { srid: string; why: string }[]; edge_types_searched: string[]; path: string;
                    hop_budget: number; conditions: string[]; ms: number; queries: string[] };
};
export type Letter = { id: string; date?: number; type?: string; from_org?: string; to_org?: string; summary?: string };
export type StatusSegment = { code: string; label: string; valid_from: number; valid_to?: number; basis: string; exact: boolean; letter?: string };
export type RecPage = {
  rec: { srid: string; text: string; status_code: string; status_label: string; is_open: boolean; closed_date?: number; issued_date: number;
         issued_from: string; priority?: string; times_reiterated: number; letter_url?: string; impact_score?: number };
  origin?: { ntsb_num?: string; city?: string; state?: string; event_date?: number; fatal?: number; display?: string; is_study?: boolean };
  origin_kind?: string; owners: { name: string; acronym?: string; status?: string; label?: string }[];
  history: StatusSegment[]; linked_crashes: { ntsb_num: string; city: string; state?: string; event_date: number; fatal: number; report: string }[];
  repeats: { how: string; date?: number; report: string; accidents: string[] }[];
  superseded_by: { srid: string; hops: number }[]; supersedes: { srid: string; hops: number }[]; letters: Letter[];
};
export type OrgPage = {
  owner: string; name: string; addressed?: number; counts: { status: string; label: string; n: number }[]; total_open: number;
  unacceptable: { srid: string; status: string; label: string; issued: number; times_reiterated: number; text: string; origin?: string }[];
  unacceptable_by_issue_period: Record<string, number>;
};
export type Step = { n: number; agent: string; tool: string; args?: unknown; ms?: number | null; wall_ms?: number; cypher?: string[] | string | null; result_count?: number | null; at?: number };
export type Verdict = {
  passed: boolean; claims_checked: number; warnings: { line: string; reason: string }[];
  blocked: { line: string; check: string; reason: string; path_searched?: string | null; weaker_path?: string | null }[];
};
export type Brief = { id: string; text: string; citations: string[]; status: string; verdict?: Verdict; thread?: string; decided_by?: string; created?: number; updated?: number };
export type Candidate = { srid: string; ntsb_num: string; score: number; evidence: Record<string, number>; status: string };
export type RunResult = {
  status: string; paused: boolean; investigation: string; thread: string; brief: Brief;
  facts: { plan?: { intent: string; place?: string; ntsb_num?: string; year?: number; srid?: string; acronym?: string };
           accident?: Accident & { reports?: string[] }; open_warnings?: unknown[]; absence?: AccidentPage["absence"];
           did_you_mean?: (Accident & { reports?: string[] })[]; chain?: unknown; backlog?: { total_open: number; counts: OrgPage["counts"] } };
  calls: { tool: string; args: Record<string, unknown>; result: { candidates?: { id: string; score: number; signals: Record<string, number> }[] } }[];
  steps: Step[]; tasks: { id: string; kind: string; status: string; claimed_by?: string; attempts?: number }[];
};
export type Investigation = { id: string; question: string; status: string; outcome?: string; started: number; finished?: number;
  steps: Step[]; tasks: RunResult["tasks"]; briefs: Brief[]; candidates: (Candidate & { evidence: string })[] };
export type Insights = {
  crashes: { ntsb_num: string; city: string; state?: string; event_date: number; fatal: number; open_warnings: string[]; lat?: number; lon?: number }[];
  linked_crashes: number; links: number; killed: number; recommendations: number;
  leaders: { srid: string; status: string; code: string; times_reiterated: number; impact: number; text: string }[];
  most_repeated_unacceptable: { srid: string; times_reiterated: number; text: string }[];
  filters: { srid: string; crashes: number }[]; origins: { srid: string; ntsb_num: string; city: string; state?: string; lat?: number; lon?: number }[];
};
