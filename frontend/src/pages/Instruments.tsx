import type { Session } from "@supabase/supabase-js";
import { useQuery } from "@tanstack/react-query";
import { fetchInstruments } from "../lib/api";
import type { Instrument } from "../types";

interface InstrumentsProps {
  session: Session;
  onSelect: (instrument: Instrument) => void;
}

export default function Instruments({ session, onSelect }: InstrumentsProps) {
  const query = useQuery({
    queryKey: ["instruments"],
    queryFn: () => fetchInstruments(session.access_token),
  });

  if (query.isLoading) return <p>Loading instruments...</p>;
  if (query.isError) return <p>Error loading instruments: {(query.error as Error).message}</p>;

  return (
    <div>
      <h2>Instruments</h2>
      <table style={{ borderCollapse: "collapse", width: "100%" }}>
        <thead>
          <tr>
            <th style={{ textAlign: "left", borderBottom: "2px solid #ccc", padding: "6px 10px" }}>
              Symbol
            </th>
            <th style={{ textAlign: "left", borderBottom: "2px solid #ccc", padding: "6px 10px" }}>
              Exchange
            </th>
            <th style={{ textAlign: "left", borderBottom: "2px solid #ccc", padding: "6px 10px" }}>
              Currency
            </th>
            <th style={{ textAlign: "left", borderBottom: "2px solid #ccc", padding: "6px 10px" }}>
              Last bar (UTC)
            </th>
            <th style={{ textAlign: "left", borderBottom: "2px solid #ccc", padding: "6px 10px" }}>
              Bars
            </th>
          </tr>
        </thead>
        <tbody>
          {query.data?.map((inst) => (
            <tr key={inst.id} onClick={() => onSelect(inst)} style={{ cursor: "pointer" }}>
              <td style={{ borderBottom: "1px solid #eee", padding: "6px 10px" }}>{inst.symbol}</td>
              <td style={{ borderBottom: "1px solid #eee", padding: "6px 10px" }}>{inst.exchange}</td>
              <td style={{ borderBottom: "1px solid #eee", padding: "6px 10px" }}>{inst.currency}</td>
              <td style={{ borderBottom: "1px solid #eee", padding: "6px 10px" }}>
                {inst.latest_bar_ts ?? "—"}
              </td>
              <td style={{ borderBottom: "1px solid #eee", padding: "6px 10px" }}>{inst.bar_count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
