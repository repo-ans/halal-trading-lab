import type { Session } from "@supabase/supabase-js";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import DemoBanner from "./components/DemoBanner";
import Disclaimer from "./components/Disclaimer";
import ThemeToggle from "./components/ThemeToggle";
import { apiFetch } from "./lib/api";
import { supabase } from "./lib/supabase";
import { useTheme } from "./lib/theme";
import Dashboard from "./pages/Dashboard";
import InstrumentChart from "./pages/InstrumentChart";
import Login from "./pages/Login";
import ReplayTrading from "./pages/ReplayTrading";
import StrategyLab from "./pages/StrategyLab";
import VirtualTrades from "./pages/VirtualTrades";
import Watchlist from "./pages/Watchlist";
import type { Instrument } from "./types";

// No router (Stage B2 decision, still true): a plain tab switch, same as how
// Watchlist/InstrumentChart already toggle via local state.
type Screen = "watchlist" | "strategy-lab" | "virtual-trades" | "dashboard" | "replay-trading";

function Home({ session, theme, onToggleTheme }: {
  session: Session; theme: ReturnType<typeof useTheme>[0]; onToggleTheme: () => void;
}) {
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
      <div className="topbar">
        <div className="brand">
          <span className="brand-dot" />
          Halal Trading Lab
        </div>

        {me.data && (
          <nav className="nav-pills">
            {([
              ["watchlist", "Watchlist"],
              ["strategy-lab", "Strategy Lab"],
              ["replay-trading", "Replay Trading"],
              ["virtual-trades", "Virtual Trades"],
              ["dashboard", "Dashboard"],
            ] as const).map(([key, label]) => (
              <button
                key={key}
                className="nav-pill"
                disabled={screen === key}
                onClick={() => goToScreen(key)}
              >
                {label}
              </button>
            ))}
          </nav>
        )}

        <div className="topbar-right">
          <span className="user-chip">
            {me.isLoading && "Checking session..."}
            {me.isError && "API error"}
            {me.data && (me.data.email ?? me.data.id)}
          </span>
          <ThemeToggle theme={theme} onToggle={onToggleTheme} />
          <button className="icon-btn" onClick={() => supabase.auth.signOut()} title="Sign out">
            ⏻
          </button>
        </div>
      </div>

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
          onGoToWatchlist={() => goToScreen("watchlist")}
        />
      ) : screen === "replay-trading" ? (
        <ReplayTrading session={session} />
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
  const [theme, toggleTheme] = useTheme();

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
      <main className="app-shell">
        {session ? (
          <Home session={session} theme={theme} onToggleTheme={toggleTheme} />
        ) : (
          <Login />
        )}
        <Disclaimer />
      </main>
    </>
  );
}
