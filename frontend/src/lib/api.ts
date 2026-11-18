import { API_URL } from "./supabase";
import type { Bar, ComplianceStatus, CsvImportResult, Instrument, WatchlistRow } from "../types";

async function _errorFor(res: Response): Promise<Error> {
  const detail = await res.json().catch(() => null);
  return new Error(`API ${res.status}${detail?.detail ? `: ${detail.detail}` : ""}`);
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

export function postCsvDryRun(file: File, token: string): Promise<CsvImportResult> {
  return postFile<CsvImportResult>("/compliance/csv/dry-run", file, token);
}

export function postCsvConfirm(file: File, token: string): Promise<{ applied: number }> {
  return postFile<{ applied: number }>("/compliance/csv/confirm", file, token);
}
