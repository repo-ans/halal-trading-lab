import type { WatchlistRow } from "../types";

const EXPIRING_SOON_DAYS = 14;

const BLOCKED_LABELS: Record<string, string> = {
  NON_COMPLIANT: "Not halal",
  QUESTIONABLE: "Doubtful",
  UNRATED: "Not covered",
  expired: "Expired",
};

function badgeFor(row: WatchlistRow): { variant: string; label: string } {
  if (row.blocking_reason === "no_record") {
    return { variant: "badge-neutral", label: "No record" };
  }
  if (row.blocking_reason !== null) {
    return {
      variant: "badge-danger",
      label: BLOCKED_LABELS[row.blocking_reason] ?? row.blocking_reason,
    };
  }
  // Tradable (halal and valid) -- amber if expiring soon, green otherwise.
  if (row.days_until_expiry !== null && row.days_until_expiry <= EXPIRING_SOON_DAYS) {
    return { variant: "badge-warning", label: `Expires in ${row.days_until_expiry}d` };
  }
  return { variant: "badge-success", label: "Halal" };
}

export default function ComplianceBadge({ row }: { row: WatchlistRow }) {
  const { variant, label } = badgeFor(row);
  return <span className={`badge ${variant}`}>{label}</span>;
}
