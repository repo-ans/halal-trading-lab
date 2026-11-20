// Pure formatting for Dashboard.tsx's metric cards. null means "no trades"
// (Stage F plan correction 3: the API returns null, never the string "no
// trades" itself -- this is the one place that gets turned into display text).
export function formatPercent(value: string | null): string {
  return value === null ? "no trades" : `${(Number(value) * 100).toFixed(2)}%`;
}

export function formatMoney(value: string | null): string {
  return value === null ? "no trades" : `$${value}`;
}
