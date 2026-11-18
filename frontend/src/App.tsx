import type { Session } from "@supabase/supabase-js";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import Disclaimer from "./components/Disclaimer";
import { API_URL, supabase } from "./lib/supabase";
import Login from "./pages/Login";

function Home({ session }: { session: Session }) {
  const me = useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      const res = await fetch(`${API_URL}/me`, {
        headers: { Authorization: `Bearer ${session.access_token}` },
      });
      if (!res.ok) throw new Error(`API ${res.status}`);
      return (await res.json()) as { id: string; email: string | null };
    },
  });

  return (
    <div>
      <h1>Halal Trading Lab</h1>
      <p>
        {me.isLoading && "Checking session with API..."}
        {me.isError && `API error: ${(me.error as Error).message}`}
        {me.data && `Signed in as ${me.data.email ?? me.data.id}`}
      </p>
      <p>Stage A scaffold. Screens arrive in later stages.</p>
      <button onClick={() => supabase.auth.signOut()}>Sign out</button>
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
