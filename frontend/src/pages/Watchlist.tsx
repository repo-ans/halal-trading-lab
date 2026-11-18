import type { Session } from "@supabase/supabase-js";
import { useQuery } from "@tanstack/react-query";
import ComplianceBadge from "../components/ComplianceBadge";
import ComplianceForm from "../components/ComplianceForm";
import CsvImport from "../components/CsvImport";
import { fetchInstruments, fetchWatchlist } from "../lib/api";
import type { Instrument } from "../types";

interface WatchlistProps {
  session: Session;
  isAdmin: boolean;
  onSelect: (instrument: Instrument) => void;
}

const th = { textAlign: "left" as const, borderBottom: "2px solid #ccc", padding: "6px 10px" };
const td = { borderBottom: "1px solid #eee", padding: "6px 10px" };

export default function Watchlist({ session, isAdmin, onSelect }: WatchlistProps) {
  const watchlistQuery = useQuery({
    queryKey: ["watchlist"],
    queryFn: () => fetchWatchlist(session.access_token),
  });
  // GET /watchlist doesn't carry currency/timezone/bar_count (InstrumentChart needs
  // those) -- reuse the already-built /instruments list and join locally by id,
  // rather than widening the watchlist response or adding a second chart-fetch path.
  const instrumentsQuery = useQuery({
    queryKey: ["instruments"],
    queryFn: () => fetchInstruments(session.access_token),
  });

  if (watchlistQuery.isLoading || instrumentsQuery.isLoading) return <p>Loading watchlist...</p>;
  if (watchlistQuery.isError) {
    return <p>Error loading watchlist: {(watchlistQuery.error as Error).message}</p>;
  }
  if (instrumentsQuery.isError) {
    return <p>Error loading instruments: {(instrumentsQuery.error as Error).message}</p>;
  }

  const instrumentsById = new Map((instrumentsQuery.data ?? []).map((i) => [i.id, i]));
  const rows = watchlistQuery.data ?? [];

  return (
    <div>
      <h2>Watchlist</h2>

      {isAdmin ? (
        <>
          <ComplianceForm token={session.access_token} rows={rows} />
          <CsvImport token={session.access_token} />
        </>
      ) : (
        <p style={{ fontSize: 12, color: "#666" }}>
          Read-only -- compliance edits require an admin account.
        </p>
      )}

      <table style={{ borderCollapse: "collapse", width: "100%" }}>
        <thead>
          <tr>
            <th style={th}>Symbol</th>
            <th style={th}>Exchange</th>
            <th style={th}>Status</th>
            <th style={th}>Last checked</th>
            <th style={th}>Expires</th>
            <th style={th}>Evidence</th>
            <th style={th}>Data timestamp</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const instrument = instrumentsById.get(row.id);
            return (
              <tr
                key={row.id}
                onClick={() => instrument && onSelect(instrument)}
                style={{ cursor: instrument ? "pointer" : "default" }}
              >
                <td style={td}>{row.symbol}</td>
                <td style={td}>{row.exchange}</td>
                <td style={td}>
                  <ComplianceBadge row={row} />
                </td>
                <td style={td}>{row.last_checked_at ?? "—"}</td>
                <td style={td}>{row.expires_at ?? "—"}</td>
                <td style={td}>
                  {row.evidence_url ? (
                    <a
                      href={row.evidence_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      onClick={(e) => e.stopPropagation()}
                    >
                      View
                    </a>
                  ) : (
                    "—"
                  )}
                </td>
                <td style={td}>{row.latest_bar_ts ?? "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
