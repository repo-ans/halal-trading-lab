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
      <h2 className="section-title">Watchlist</h2>

      {isAdmin ? (
        <>
          <ComplianceForm token={session.access_token} rows={rows} />
          <CsvImport token={session.access_token} />
        </>
      ) : (
        <p className="subtle">
          Read-only -- compliance edits require an admin account.
        </p>
      )}

      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Exchange</th>
              <th>Status</th>
              <th>Last checked</th>
              <th>Expires</th>
              <th>Evidence</th>
              <th>Data timestamp</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const instrument = instrumentsById.get(row.id);
              return (
                <tr
                  key={row.id}
                  onClick={() => instrument && onSelect(instrument)}
                  className={instrument ? "clickable" : ""}
                >
                  <td>{row.symbol}</td>
                  <td>{row.exchange}</td>
                  <td>
                    <ComplianceBadge row={row} />
                  </td>
                  <td>{row.last_checked_at ?? "—"}</td>
                  <td>{row.expires_at ?? "—"}</td>
                  <td>
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
                  <td>{row.latest_bar_ts ?? "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
