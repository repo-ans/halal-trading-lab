import type { Session } from "@supabase/supabase-js";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import DemoBanner from "./components/DemoBanner";
import Disclaimer from "./components/Disclaimer";
import { apiFetch } from "./lib/api";
import { supabase } from "./lib/supabase";
import Dashboard from "./pages/Dashboard";
import InstrumentChart from "./pages/InstrumentChart";
import Login from "./pages/Login";
import StrategyLab from "./pages/StrategyLab";
import VirtualTrades from "./pages/VirtualTrades";
import Watchlist from "./pages/Watchlist";
import type { Instrument } from "./types";

// No router (Stage B2 decision, still true): a plain tab switch, same as how
// Watchlist/InstrumentChart already toggle via local state.
type Screen = "watchlist" | "strategy-lab" | "virtual-trades" | "dashboard";

function Home({ session }: { session: Session }) {
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () =>
      apiFetch<{ id: string; email: string | null; is_admin: boolean }>("/me", session.access_token),
  });
  const [screen, setScreen] = useState<Screen>("watchlist");
  const [selectedInstrument, setSelectedInstrument] = useState<Instrument | null>(null);
  const [activeRunId, setActiveRunId] = useState<number | null>(null);

  function goToScreen(next: Screen) {
    setSelectedInstrument(null);
    setScreen(next);
  }

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <h1>Halal Trading Lab</h1>
        <div style={{ fontSize: 13, color: "#555" }}>
          {me.isLoading && "Checking session with API..."}
          {me.isError && `API error: ${(me.error as Error).message}`}
          {me.data && `Signed in as ${me.data.email ?? me.data.id}`}{" "}
          <button onClick={() => supabase.auth.signOut()}>Sign out</button>
        </div>
      </div>

      {me.data && (
        <nav style={{ display: "flex", gap: 8, margin: "12px 0" }}>
          {([
            ["watchlist", "Watchlist"],
            ["strategy-lab", "Strategy Lab"],
            ["virtual-trades", "Virtual Trades"],
            ["dashboard", "Dashboard"],
          ] as const).map(([key, label]) => (
            <button key={key} disabled={screen === key} onClick={() => goToScreen(key)}>
              {label}
            </button>
          ))}
        </nav>
      )}

      {!me.data ? null : selectedInstrument ? (
        <InstrumentChart
          session={session}
          instrument={selectedInstrument}
          onBack={() => setSelectedInstrument(null)}
        />
      ) : screen === "watchlist" ? (
        <Watchlist session={session} isAdmin={me.data.is_admin} onSelect={setSelectedInstrument} />
      ) : screen === "strategy-lab" ? (
        <StrategyLab
          session={session}
          onRunReady={(runId) => {
            setActiveRunId(runId);
            goToScreen("virtual-trades");
          }}
        />
      ) : screen === "virtual-trades" ? (
        <VirtualTrades session={session} runId={activeRunId} onSelectRun={setActiveRunId} />
      ) : (
        <Dashboard session={session} runId={activeRunId} onSelectRun={setActiveRunId} />
      )}
    </div>
  );
}

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setReady(true);
    });
    const { data } = supabase.auth.onAuthStateChange((_e, s) => setSession(s));
    return () => data.subscription.unsubscribe();
  }, []);

  if (!ready) return null;
  return (
    <>
      <DemoBanner />
      <main style={{ fontFamily: "system-ui", padding: 24 }}>
        {session ? <Home session={session} /> : <Login />}
        <Disclaimer />
      </main>
    </>
  );
}
