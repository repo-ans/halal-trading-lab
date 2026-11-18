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
