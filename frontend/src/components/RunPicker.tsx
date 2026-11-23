import { useQuery } from "@tanstack/react-query";
import { fetchStrategyRuns } from "../lib/api";

interface RunPickerProps {
  token: string;
  selectedRunId: number | null;
  onSelect: (runId: number | null) => void;
}

// GET /strategy-runs: own runs only (Stage F) -- shared by Virtual Trades and
// the Dashboard so both pick from the same list the same way.
export default function RunPicker({ token, selectedRunId, onSelect }: RunPickerProps) {
  const query = useQuery({
    queryKey: ["strategy-runs"],
    queryFn: () => fetchStrategyRuns(token, { limit: 100 }),
  });

  if (query.isLoading) return <p className="subtle">Loading your runs...</p>;
  if (query.isError) return <p className="error-text">Error loading runs: {(query.error as Error).message}</p>;

  const runs = query.data?.items ?? [];
  if (runs.length === 0) return <p className="subtle">You haven't run a strategy yet -- start one in Strategy Lab.</p>;

  return (
    <select
      value={selectedRunId ?? ""}
      onChange={(e) => onSelect(e.target.value ? Number(e.target.value) : null)}
      style={{ minWidth: 260 }}
    >
      <option value="">Select a run...</option>
      {runs.map((r) => (
        <option key={r.id} value={r.id}>
          #{r.id} -- {r.range_start.slice(0, 10)} to {r.range_end.slice(0, 10)}
          {r.status === "failed" ? " (failed)" : ""}
        </option>
      ))}
    </select>
  );
}
