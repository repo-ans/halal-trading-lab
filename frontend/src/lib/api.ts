import { API_URL } from "./supabase";
import type {
  AiSummary,
  AiSummaryMode,
  Bar,
  ComplianceStatus,
  CsvImportResult,
  Instrument,
  Metrics,
  PaperOrdersPage,
  PaperRunResult,
  ReplayFill,
  ReplayState,
  StrategyRun,
  StrategyRunListResult,
  WatchlistRow,
} from "../types";

// Carries the RAW `detail` from the response body (string, object, array, or
// absent) alongside an always-safe `.message` string, so a caller that just
// shows `.message` can never render "[object Object]", while a caller that
// wants to interpret a specific error shape (e.g. StrategyLab.tsx's
// "all_instruments_blocked") can read `.detail` for the real structure.
export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

// Depth-capped on purpose: this only ever has to flatten small JSON error
// bodies (a FastAPI validation-error list, or an unexpected object shape),
// never arbitrary app data.
function extractStrings(value: unknown, depth = 0): string[] {
  if (depth > 4) return [];
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.flatMap((v) => extractStrings(v, depth + 1));
  if (value && typeof value === "object") {
    return Object.values(value as Record<string, unknown>).flatMap((v) => extractStrings(v, depth + 1));
  }
  return [];
}

async function _errorFor(res: Response): Promise<ApiError> {
  const body = await res.json().catch(() => null);
  const detail = body?.detail;

  let message: string;
  if (typeof detail === "string") {
    message = `API ${res.status}: ${detail}`;
  } else if (detail !== undefined && detail !== null) {
    // Never template-literal an object/array straight into the message --
    // that stringifies to "[object Object]". Pull out whatever plain-text
    // pieces it contains instead; a caller that understands this specific
    // error's shape (e.g. StrategyLab.tsx) should read `.detail` directly
    // rather than relying on this generic fallback text.
    const strings = extractStrings(detail).map((s) => s.trim()).filter(Boolean);
    message = strings.length > 0
      ? `Request failed (${res.status}): ${strings.join(", ")}`
      : `Request failed (${res.status})`;
  } else {
    message = `API ${res.status}`;
  }

  return new ApiError(res.status, detail, message);
}

export async function apiFetch<T>(path: string, token: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw await _errorFor(res);
  return (await res.json()) as T;
}

async function postJson<T>(path: string, body: unknown, token: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await _errorFor(res);
  return (await res.json()) as T;
}

async function postFile<T>(path: string, file: File, token: string): Promise<T> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  if (!res.ok) throw await _errorFor(res);
  return (await res.json()) as T;
}

export function fetchInstruments(token: string): Promise<Instrument[]> {
  return apiFetch<Instrument[]>("/instruments", token);
}

export function fetchBarsRange(
  instrumentId: number,
  fromEpochSeconds: number,
  toEpochSeconds: number,
  token: string,
): Promise<Bar[]> {
  const params = new URLSearchParams({
    from: String(fromEpochSeconds),
    to: String(toEpochSeconds),
  });
  return apiFetch<Bar[]>(`/instruments/${instrumentId}/bars?${params}`, token);
}

// The API caps a single request at 5000 bars (422 if exceeded) -- that cap stays
// server-side and is the message shown for a manual range. "All" must never hit it:
// walk backward from the latest bar in fixed-size windows and concatenate, so the
// view keeps working as data accumulates for months or years, not just today.
//
// 50 calendar days is conservative on purpose: at most ~78 bars/trading day, so even
// a window with no weekends/holidays (impossible, but the bound should hold
// regardless) stays under 50 * 78 = 3900, safely under the 4000 target.
const ALL_WINDOW_SECONDS = 50 * 24 * 60 * 60;
const ALL_MAX_WINDOWS = 60; // safety cap: ~8 years of calendar time at 50-day steps

export async function fetchAllBars(
  instrumentId: number,
  latestBarTs: string | null,
  barCount: number,
  token: string,
): Promise<Bar[]> {
  if (!latestBarTs || barCount === 0) return [];

  let to = Math.floor(new Date(latestBarTs).getTime() / 1000) + 1; // +1: to is exclusive
  const windows: Bar[][] = [];
  let collected = 0;
  let consecutiveEmpty = 0;

  for (let i = 0; i < ALL_MAX_WINDOWS && collected < barCount; i++) {
    const from = to - ALL_WINDOW_SECONDS;
    const bars = await fetchBarsRange(instrumentId, from, to, token);
    windows.push(bars);
    collected += bars.length;
    consecutiveEmpty = bars.length === 0 ? consecutiveEmpty + 1 : 0;
    // Two consecutive empty windows (100 days of nothing) means we're past the
    // earliest stored data -- stop, rather than running all the way to the safety cap.
    if (consecutiveEmpty >= 2) break;
    to = from;
  }

  return windows.reverse().flat();
}

export function fetchWatchlist(token: string): Promise<WatchlistRow[]> {
  return apiFetch<WatchlistRow[]>("/watchlist", token);
}

export interface AppConfig {
  app_mode: "production" | "demo";
}

// Unauthenticated -- same tier as /health. Needs to work before sign-in so the
// demo banner can render on the login screen too.
export async function fetchConfig(): Promise<AppConfig> {
  const res = await fetch(`${API_URL}/config`);
  if (!res.ok) throw await _errorFor(res);
  return (await res.json()) as AppConfig;
}

export interface ComplianceInput {
  instrument_id: number;
  status: ComplianceStatus;
  source: "manual" | "zoya" | "musaffa";
  evidence_url?: string | null;
  notes?: string | null;
  checked_at: string; // ISO-8601 with an explicit offset
  expires_at?: string | null;
}

export function postCompliance(input: ComplianceInput, token: string): Promise<unknown> {
  return postJson("/compliance", input, token);
}

// A SUGGESTION only (admin-only route) -- never writes anything itself. The
// caller still fills in real evidence and submits through postCompliance
// above. is_sandbox=true means Zoya's own randomized dummy data, never a
// real ruling -- the UI must say so, not just the API.
export interface ZoyaLookupResult {
  available: boolean;
  is_sandbox: boolean;
  status: ComplianceStatus | null;
  purification_ratio: number | null;
  report_date: string | null;
  zoya_name: string | null;
  zoya_exchange: string | null;
}

export function fetchZoyaLookup(instrumentId: number, token: string): Promise<ZoyaLookupResult> {
  return apiFetch<ZoyaLookupResult>(`/compliance/zoya-lookup?instrument_id=${instrumentId}`, token);
}

export function postCsvDryRun(file: File, token: string): Promise<CsvImportResult> {
  return postFile<CsvImportResult>("/compliance/csv/dry-run", file, token);
}

export function postCsvConfirm(file: File, token: string): Promise<{ applied: number }> {
  return postFile<{ applied: number }>("/compliance/csv/confirm", file, token);
}

// --- Stage F: Strategy Lab, Virtual Trades, Dashboard ---

export interface StrategyRunInput {
  instrument_ids: number[];
  range_start: string; // ISO-8601 with an explicit offset
  range_end: string;
  fast_period: number;
  slow_period: number;
  currency: string;
  starting_cash: string;
  max_position_value: string;
  commission_per_order: string;
  spread_bps: string;
  slippage_bps: string;
  reset_ma_each_session: boolean;
}

export function createStrategyRun(input: StrategyRunInput, token: string): Promise<StrategyRun> {
  return postJson("/strategy-runs", input, token);
}

export function fetchStrategyRuns(
  token: string,
  opts: { limit?: number; offset?: number } = {},
): Promise<StrategyRunListResult> {
  const params = new URLSearchParams();
  if (opts.limit) params.set("limit", String(opts.limit));
  if (opts.offset) params.set("offset", String(opts.offset));
  return apiFetch<StrategyRunListResult>(`/strategy-runs?${params}`, token);
}

export function fetchStrategyRun(runId: number, token: string): Promise<StrategyRun> {
  return apiFetch<StrategyRun>(`/strategy-runs/${runId}`, token);
}

// {} body -- the route takes no input, but postJson always sends a JSON body.
export function postPaperRun(runId: number, token: string): Promise<PaperRunResult> {
  return postJson<PaperRunResult>(`/strategy-runs/${runId}/paper-run`, {}, token);
}

export function fetchMetrics(runId: number, token: string): Promise<Metrics> {
  return apiFetch<Metrics>(`/strategy-runs/${runId}/metrics`, token);
}

export function fetchPaperOrders(
  runId: number,
  token: string,
  opts: { limit?: number; offset?: number; instrumentId?: number; status?: string } = {},
): Promise<PaperOrdersPage> {
  const params = new URLSearchParams();
  if (opts.limit) params.set("limit", String(opts.limit));
  if (opts.offset) params.set("offset", String(opts.offset));
  if (opts.instrumentId) params.set("instrument_id", String(opts.instrumentId));
  if (opts.status) params.set("status", opts.status);
  return apiFetch<PaperOrdersPage>(`/strategy-runs/${runId}/orders?${params}`, token);
}

export function postAiSummary(runId: number, mode: AiSummaryMode, token: string): Promise<AiSummary> {
  return postJson<AiSummary>(`/strategy-runs/${runId}/ai-summary`, { mode }, token);
}

// null, not a throw, on 404 -- "no report generated yet for this mode" is an
// expected, common state (handled as a plain empty-ish state by the caller),
// not an error.
export async function fetchAiSummary(
  runId: number, mode: AiSummaryMode, token: string,
): Promise<AiSummary | null> {
  const res = await fetch(`${API_URL}/strategy-runs/${runId}/ai-summary?mode=${mode}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (res.status === 404) return null;
  if (!res.ok) throw await _errorFor(res);
  return (await res.json()) as AiSummary;
}

// --- Replay Trading: manual Buy/Sell against a replaying chart ---

export interface ReplaySessionInput {
  instrument_id: number;
  session_day: string; // YYYY-MM-DD
  starting_cash?: string;
  max_position_value?: string;
  commission_per_order?: string;
  spread_bps?: string;
  slippage_bps?: string;
}

export function createReplaySession(input: ReplaySessionInput, token: string): Promise<ReplayState> {
  return postJson("/replay-sessions", input, token);
}

export function fetchReplaySessionState(sessionId: number, token: string): Promise<ReplayState> {
  return apiFetch<ReplayState>(`/replay-sessions/${sessionId}`, token);
}

export function fetchReplayFills(sessionId: number, token: string): Promise<ReplayFill[]> {
  return apiFetch<ReplayFill[]>(`/replay-sessions/${sessionId}/fills`, token);
}

// clickedBarTs: ISO-8601 with an explicit offset -- the bar the user was
// looking at when they clicked. The backend fills at the NEXT bar's open in
// the same session, never this bar's own close (no-look-ahead, CLAUDE.md
// section 7) -- there is no "side: reverse"; long-only means closing a
// position is simply "sell".
export function postReplayOrder(
  sessionId: number, side: "buy" | "sell", clickedBarTs: string, token: string,
): Promise<ReplayState> {
  return postJson<ReplayState>(
    `/replay-sessions/${sessionId}/orders`, { side, clicked_bar_ts: clickedBarTs }, token);
}

export function endReplaySession(sessionId: number, token: string): Promise<ReplayState> {
  return postJson<ReplayState>(`/replay-sessions/${sessionId}/end`, {}, token);
}

// Authenticated file download: a plain <a href> can't carry a bearer token, so
// this fetches with the header, then hands the browser a blob to save.
export async function downloadCsv(path: string, token: string, filename: string): Promise<void> {
  const res = await fetch(`${API_URL}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw await _errorFor(res);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
