import type { WatchlistRow } from "../types";

const EXPIRING_SOON_DAYS = 14;

const BLOCKED_LABELS: Record<string, string> = {
  not_halal: "Not halal",
  doubtful: "Doubtful",
  not_covered: "Not covered",
  expired: "Expired",
};

function badgeFor(row: WatchlistRow): { bg: string; fg: string; label: string } {
  if (row.blocking_reason === "no_record") {
    return { bg: "#e2e3e5", fg: "#41464b", label: "No record" };
  }
  if (row.blocking_reason !== null) {
    return {
      bg: "#f8d7da",
      fg: "#58151c",
      label: BLOCKED_LABELS[row.blocking_reason] ?? row.blocking_reason,
    };
  }
  // Tradable (halal and valid) -- amber if expiring soon, green otherwise.
  if (row.days_until_expiry !== null && row.days_until_expiry <= EXPIRING_SOON_DAYS) {
    return { bg: "#fff3cd", fg: "#664d03", label: `Expires in ${row.days_until_expiry}d` };
  }
  return { bg: "#d1e7dd", fg: "#0a3622", label: "Halal" };
}

export default function ComplianceBadge({ row }: { row: WatchlistRow }) {
  const { bg, fg, label } = badgeFor(row);
  return (
    <span
      style={{
        background: bg,
        color: fg,
        borderRadius: 4,
        padding: "2px 8px",
        fontSize: 12,
        fontWeight: 600,
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </span>
  );
}
