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
  latest_close: string | null; // Stage F: Strategy Lab's price-vs-cap warning
  // Stage H: true means the price exists but is withheld (yfinance-sourced,
  // caller isn't the licensed owner) -- never confuse this with "no data".
  latest_close_restricted: boolean;
  evidence_url: string | null;
}

// Matches backend/app/routes/strategy_runs.py's StrategyRunOut exactly.
export interface StrategyRun {
  id: number;
  status: "pending" | "running" | "completed" | "failed";
  error: string | null;
  config_hash: string;
  instrument_ids: number[];
  range_start: string;
  range_end: string;
  fast_period: number;
  slow_period: number;
  reset_ma_each_session: boolean;
  blocked_instruments: Record<string, string>;
  compliance_check_ids: Record<string, number>;
  started_at: string | null;
  finished_at: string | null;
  paper_status: "not_run" | "completed";
  paper_completed_at: string | null;
  max_position_value: string;
}

export interface StrategyRunListResult {
  items: StrategyRun[];
  total: number;
}

export interface PaperRunResult {
  id: number;
  paper_status: "not_run" | "completed";
  paper_completed_at: string | null;
  order_count: number;
  fill_count: number;
}

export type PaperOrderStatus = "filled" | "rejected" | "cancelled";

export interface PaperOrder {
  id: number;
  instrument_id: number;
  signal_id: number | null;
  origin: "signal" | "eod_exit";
  side: "buy" | "sell";
  qty: string;
  intended_price: string | null;
  execution_ts: string;
  status: PaperOrderStatus;
  rejection_reason: string | null;
  rejection_message: string | null; // plain language, same text the CSV export uses
}

export interface PaperOrdersPage {
  items: PaperOrder[];
  total: number;
}

export interface EquityPoint {
  ts: string;
  cash: string;
}

// Decimal-as-string when defined; null means "no trades" (or, for avg_win/
// avg_non_win, "no trades in that specific bucket") -- the API never renders
// that as a string itself (Stage F plan correction 3).
export type AiSummaryMode = "template" | "llm";

// Matches backend/app/routes/strategy_runs.py's AiSummaryOut exactly.
export interface AiSummary {
  id: number;
  mode: AiSummaryMode; // what was actually rendered -- never "llm" for a fallback
  requested_mode: AiSummaryMode;
  heading: string;
  report_text: string;
  llm_attempts: number;
  generated_at: string;
  fallback_occurred: boolean;
}

export interface Metrics {
  trades_count: number;
  realized_pnl: string | null;
  total_return: string | null;
  win_rate: string | null;
  avg_win: string | null;
  avg_non_win: string | null;
  total_fees: string;
  max_drawdown: string;
  unresolved_positions: number;
  unresolved_instrument_ids: number[];
  equity_curve: EquityPoint[];
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
