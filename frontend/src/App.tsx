import type { Session } from "@supabase/supabase-js";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import Disclaimer from "./components/Disclaimer";
import { apiFetch } from "./lib/api";
import { supabase } from "./lib/supabase";
import InstrumentChart from "./pages/InstrumentChart";
import Instruments from "./pages/Instruments";
import Login from "./pages/Login";
import type { Instrument } from "./types";

function Home({ session }: { session: Session }) {
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => apiFetch<{ id: string; email: string | null }>("/me", session.access_token),
  });
  const [selected, setSelected] = useState<Instrument | null>(null);

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

      {selected ? (
        <InstrumentChart session={session} instrument={selected} onBack={() => setSelected(null)} />
      ) : (
        <Instruments session={session} onSelect={setSelected} />
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
    <main style={{ fontFamily: "system-ui", padding: 24 }}>
      {session ? <Home session={session} /> : <Login />}
      <Disclaimer />
    </main>
  );
}
