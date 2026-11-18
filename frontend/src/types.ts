// Matches backend/app/routes/instruments.py's InstrumentOut/BarOut exactly.
export interface Instrument {
  id: number;
  symbol: string;
  exchange: string;
  currency: string;
  timezone: string; // IANA name, from the backend -- never hardcoded here
  latest_bar_ts: string | null; // ISO-8601 UTC
  bar_count: number;
  compliance: null; // Stage C wires this up
}

// open/high/low/close are Decimal strings from the API -- convert with Number() only
// at the point a chart needs a number, never earlier (CLAUDE.md: Decimal/NUMERIC
// throughout; this is the one place we deliberately leave that contract for display).
export interface Bar {
  ts: number; // epoch seconds UTC
  open: string;
  high: string;
  low: string;
  close: string;
  volume: number;
  source: string;
}

export type ComplianceStatus = "halal" | "not_halal" | "doubtful" | "not_covered";
export type BlockingReason = ComplianceStatus | "no_record" | "expired";

// Matches backend/app/routes/watchlist.py's WatchlistRow exactly. The app never
// computes a halal status itself -- `status` is only ever what a human recorded.
export interface WatchlistRow {
  id: number;
  symbol: string;
  exchange: string;
  status: ComplianceStatus | null;
  blocking_reason: BlockingReason | null; // null means tradable
  last_checked_at: string | null;
  expires_at: string | null;
  days_until_expiry: number | null;
  latest_bar_ts: string | null;
  evidence_url: string | null;
}

export interface CsvRowResult {
  row_number: number;
  accepted: boolean;
  symbol: string | null;
  exchange: string | null;
  reason: string | null;
}

export interface CsvImportResult {
  rows: CsvRowResult[];
  all_accepted: boolean;
}
